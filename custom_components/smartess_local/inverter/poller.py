"""Per-command priority polling loop for Voltronic inverter.

Each command has its own configurable interval:
  - >0: poll every N seconds
  - -1: poll once at startup, never repeat
  -  0: disabled

Each cycle picks the most overdue command, sends it, waits for response.
Natural RS485 pacing (~1s per command round-trip) prevents bus contention.
Startup commands always run first (higher priority than regular commands).
Commands that NAK 3 times consecutively are auto-disabled.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable

from custom_components.smartess_local.protocol.p17 import (
    build_poll,
    parse_response as parse_p17_response,
)
from custom_components.smartess_local.inverter.sensors import (
    SENSOR_MAP,
    parse_response as parse_sensor_response,
)

logger = logging.getLogger(__name__)

# Type aliases
SendFunc = Callable[[bytes], Awaitable[bytes]]
ResultCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


MAX_NAK_BEFORE_DISABLE = 3


class CommandState:
    """Tracks per-command polling state."""

    __slots__ = ("cmd", "interval", "last_run", "disabled", "startup_done", "nak_count")

    def __init__(self, cmd: str, interval: int):
        self.cmd = cmd
        self.interval = interval  # >0 = seconds, -1 = once, 0 = off
        self.last_run: float = 0.0
        self.disabled = interval == 0
        self.startup_done = False
        self.nak_count: int = 0

    @property
    def is_startup_only(self) -> bool:
        return self.interval == -1

    def overdue_by(self, now: float) -> float:
        """How many seconds overdue this command is. Negative = not yet due."""
        if self.disabled:
            return -999999.0
        # Startup commands get higher priority than regular "never run" commands
        if self.is_startup_only:
            return 1_000_000.0 if not self.startup_done else -999999.0
        if self.last_run == 0.0:
            return 999_999.0  # never run = maximally overdue (but below startup)
        return now - self.last_run - self.interval

    def __repr__(self) -> str:
        return (f"CommandState({self.cmd}, interval={self.interval}, "
                f"disabled={self.disabled}, startup_done={self.startup_done}, "
                f"nak_count={self.nak_count})")


class InverterPoller:
    """Single-loop poller that picks the most overdue command each cycle.

    One poller instance per inverter (devaddr). The send_func should
    already have the devaddr baked in.
    """

    def __init__(
        self,
        send_func: SendFunc,
        on_result: ResultCallback,
        intervals: dict[str, int],
        devaddr: int = 1,
        min_command_gap: float = 0.3,
    ):
        self.send = send_func
        self.on_result = on_result
        self.devaddr = devaddr
        self.min_command_gap = min_command_gap
        self._task: asyncio.Task | None = None
        self._running = False

        self._commands: list[CommandState] = [
            CommandState(cmd, interval)
            for cmd, interval in intervals.items()
            if cmd in SENSOR_MAP
        ]

        logger.debug("[addr=%d] Poller created with intervals: %s",
                     devaddr, {cs.cmd: cs.interval for cs in self._commands})

    async def start(self) -> None:
        """Start the polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        active = {cs.cmd: cs.interval for cs in self._commands if not cs.disabled}
        logger.info("[addr=%d] Poller started: %s", self.devaddr, active)

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[addr=%d] Poller stopped", self.devaddr)

    async def refresh(self, cmd: str) -> None:
        """Force immediate re-poll of a command (e.g. after a set command)."""
        for cs in self._commands:
            if cs.cmd == cmd:
                cs.last_run = 0.0
                logger.debug("[addr=%d] Forced refresh of %s", self.devaddr, cmd)
                break

    async def _poll_loop(self) -> None:
        """Main loop: pick most overdue, execute, repeat."""
        try:
            while self._running:
                now = time.monotonic()
                best: CommandState | None = None
                best_overdue = -999999.0
                for cs in self._commands:
                    od = cs.overdue_by(now)
                    if od > best_overdue:
                        best_overdue = od
                        best = cs

                if best is None or best_overdue < 0:
                    await asyncio.sleep(0.5)
                    continue

                logger.debug("[addr=%d] Next command: %s (overdue by %.1fs)",
                             self.devaddr, best.cmd, best_overdue)
                await self._execute(best)
                await asyncio.sleep(self.min_command_gap)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("[addr=%d] Poller loop fatal: %s", self.devaddr, e, exc_info=True)

    async def _execute(self, cs: CommandState) -> None:
        """Execute a single P17 poll command."""
        try:
            p17_frame = build_poll(cs.cmd)
            logger.debug("[addr=%d] TX %s frame=%s", self.devaddr, cs.cmd, p17_frame.hex())

            raw_response = await self.send(p17_frame)

            # Raw response log — always at INFO so user can forward from HA logs
            ascii_repr = raw_response.decode("ascii", errors="replace")
            logger.info("[addr=%d] [%s] raw_hex=%s raw_ascii=%r",
                        self.devaddr, cs.cmd, raw_response.hex(), ascii_repr)

            cmd_type, response_data = parse_p17_response(raw_response)
            logger.debug("[addr=%d] [%s] parsed: type=%s data=%r",
                         self.devaddr, cs.cmd, cmd_type, response_data)

            if cmd_type == "N":  # NAK
                cs.nak_count += 1
                if cs.nak_count >= MAX_NAK_BEFORE_DISABLE:
                    logger.warning("[addr=%d] %s NAK'd %d times -- disabling",
                                   self.devaddr, cs.cmd, cs.nak_count)
                    cs.disabled = True
                else:
                    logger.info("[addr=%d] %s NAK'd (%d/%d) -- will retry",
                                self.devaddr, cs.cmd, cs.nak_count, MAX_NAK_BEFORE_DISABLE)
                    cs.last_run = time.monotonic()  # back off before retry
                return

            values = parse_sensor_response(cs.cmd, response_data)

            cs.nak_count = 0  # reset on success
            cs.last_run = time.monotonic()
            if cs.is_startup_only:
                cs.startup_done = True

            if values:
                logger.debug("[addr=%d] [%s] parsed %d values: %s",
                             self.devaddr, cs.cmd, len(values), values)
                await self.on_result(cs.cmd, values)
            else:
                logger.warning("[addr=%d] [%s] no values parsed from %r",
                               self.devaddr, cs.cmd, response_data)

        except asyncio.TimeoutError:
            logger.warning("[addr=%d] %s timed out", self.devaddr, cs.cmd)
            cs.last_run = time.monotonic()
        except ConnectionError as e:
            logger.warning("[addr=%d] %s connection error: %s", self.devaddr, cs.cmd, e)
        except ValueError as e:
            logger.warning("[addr=%d] %s parse error: %s (see raw_hex log line above)",
                           self.devaddr, cs.cmd, e)
        except Exception as e:
            logger.error("[addr=%d] %s unexpected error: %s",
                         self.devaddr, cs.cmd, e, exc_info=True)
            cs.last_run = time.monotonic()

    async def query_once(self, cmd: str) -> dict[str, Any] | None:
        """Ad-hoc single command query (outside the loop)."""
        try:
            p17_frame = build_poll(cmd)
            raw_response = await self.send(p17_frame)
            cmd_type, response_data = parse_p17_response(raw_response)
            if cmd_type == "N":
                return None
            return parse_sensor_response(cmd, response_data)
        except Exception as e:
            logger.error("[addr=%d] Query %s failed: %s", self.devaddr, cmd, e)
            return None
