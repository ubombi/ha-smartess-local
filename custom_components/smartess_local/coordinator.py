"""Coordinator for SmartESS Local integration.

Owns the full lifecycle: TCP server, UDP announcer, one poller per inverter.
Supports multiple inverters on a single collector's RS485 bus.
Provides data to HA entities via DataUpdateCoordinator push pattern.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.smartess_local.const import (
    DOMAIN,
    CONF_SERVER_IP,
    CONF_TCP_PORT,
    CONF_UDP_PORT,
    CONF_UDP_BROADCAST_IP,
    CONF_HEARTBEAT_INTERVAL,
    CONF_INVERTER_COUNT,
    CONF_POLL_FAST,
    CONF_POLL_MEDIUM,
    CONF_POLL_SLOW,
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    DEFAULT_UDP_BROADCAST_IP,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_INVERTER_COUNT,
    DEFAULT_POLL_FAST,
    DEFAULT_POLL_MEDIUM,
    DEFAULT_POLL_SLOW,
    build_poll_intervals,
)
from custom_components.smartess_local.server.tcp_server import TCPServer
from custom_components.smartess_local.server.udp_announcer import UDPAnnouncer
from custom_components.smartess_local.inverter.poller import InverterPoller
from custom_components.smartess_local.protocol.p17 import build_set, parse_response as parse_p17

logger = logging.getLogger(__name__)


def _get_local_ip() -> str:
    """Get the local IP address by connecting to a public DNS."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@dataclass
class InverterInfo:
    """Per-inverter metadata, populated from startup commands."""
    model_name: str = ""
    serial_number: str = ""
    firmware_version: str = ""


class InverterCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages TCP server, UDP announcer, and per-inverter pollers."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger,
            name=DOMAIN,
            # No update_interval -- data pushed via poller callbacks
        )
        self._entry = entry
        self._tcp: TCPServer | None = None
        self._udp: UDPAnnouncer | None = None
        self._pollers: dict[int, InverterPoller] = {}  # devaddr -> poller

        # Collector info (populated after first heartbeat)
        self.collector_pn: str = ""

        # Per-inverter data: devaddr -> {sensor_name: value}
        self.inverter_data: dict[int, dict[str, Any]] = {}
        # Per-inverter metadata
        self.inverter_info: dict[int, InverterInfo] = {}

        # Inverter addresses (1-based)
        inv_count = entry.data.get(CONF_INVERTER_COUNT, DEFAULT_INVERTER_COUNT)
        # Also check options (can be changed later)
        inv_count = entry.options.get(CONF_INVERTER_COUNT, inv_count)
        self.inverter_addresses: list[int] = list(range(1, int(inv_count) + 1))

        logger.debug("Coordinator init: entry_id=%s inverter_addresses=%s",
                      entry.entry_id, self.inverter_addresses)

        # Initialize data dicts
        for addr in self.inverter_addresses:
            self.inverter_data[addr] = {}
            self.inverter_info[addr] = InverterInfo()

    @property
    def connected(self) -> bool:
        """Whether the collector is currently connected."""
        return self._tcp is not None and self._tcp.connected

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Start TCP server, UDP announcer. Called from async_setup_entry."""
        cfg = self._entry.data
        server_ip = cfg.get(CONF_SERVER_IP, "0.0.0.0")
        tcp_port = cfg.get(CONF_TCP_PORT, DEFAULT_TCP_PORT)
        udp_port = cfg.get(CONF_UDP_PORT, DEFAULT_UDP_PORT)
        broadcast_ip = cfg.get(CONF_UDP_BROADCAST_IP, DEFAULT_UDP_BROADCAST_IP)
        heartbeat_interval = cfg.get(CONF_HEARTBEAT_INTERVAL, DEFAULT_HEARTBEAT_INTERVAL)

        logger.info("Setting up SmartESS Local coordinator: server=%s:%d udp=%d inverters=%s",
                     server_ip, tcp_port, udp_port, self.inverter_addresses)

        # TCP server
        self._tcp = TCPServer(
            host=server_ip,
            port=tcp_port,
            heartbeat_interval=float(heartbeat_interval),
            on_connect=self._on_collector_connect,
            on_disconnect=self._on_collector_disconnect,
        )
        await self._tcp.start()

        # UDP announcer
        announce_ip = server_ip if server_ip != "0.0.0.0" else _get_local_ip()
        self._udp = UDPAnnouncer(
            server_ip=announce_ip,
            server_port=tcp_port,
            broadcast_ip=broadcast_ip,
            udp_port=udp_port,
        )
        await self._udp.start()
        logger.info("Coordinator started -- waiting for collector on %s:%d", server_ip, tcp_port)

    async def async_shutdown(self) -> None:
        """Stop everything. Called from async_unload_entry."""
        logger.debug("Coordinator shutting down...")
        for addr, poller in self._pollers.items():
            logger.debug("Stopping poller for addr=%d", addr)
            await poller.stop()
        self._pollers.clear()
        if self._udp:
            await self._udp.stop()
            self._udp = None
        if self._tcp:
            await self._tcp.stop()
            self._tcp = None
        logger.info("Coordinator stopped")

    # ------------------------------------------------------------------
    # DataUpdateCoordinator required method (no-op, we push)
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Not used for periodic pulling -- data pushed via poller callback."""
        return {}

    # ------------------------------------------------------------------
    # Collector callbacks
    # ------------------------------------------------------------------

    async def _on_collector_connect(self, collector_pn: str) -> None:
        """First heartbeat received -- collector identified. Start polling."""
        self.collector_pn = collector_pn
        logger.info("Collector identified: %s -- starting pollers for %s",
                     collector_pn, self.inverter_addresses)

        # Update config entry title to show logger PN
        if collector_pn:
            self.hass.config_entries.async_update_entry(
                self._entry, title=f"Logger {collector_pn}",
            )

        # Stop UDP announcer (collector is here)
        if self._udp:
            await self._udp.stop()

        # Build poll intervals from options (or defaults)
        opts = self._entry.options
        intervals = build_poll_intervals(
            fast=opts.get(CONF_POLL_FAST, DEFAULT_POLL_FAST),
            medium=opts.get(CONF_POLL_MEDIUM, DEFAULT_POLL_MEDIUM),
            slow=opts.get(CONF_POLL_SLOW, DEFAULT_POLL_SLOW),
        )
        logger.debug("Poll intervals: %s", intervals)

        # Start one poller per inverter address
        for addr in self.inverter_addresses:
            send_func = self._make_send_func(addr)
            result_cb = self._make_result_callback(addr)
            poller = InverterPoller(
                send_func=send_func,
                on_result=result_cb,
                intervals=intervals,
                devaddr=addr,
            )
            self._pollers[addr] = poller
            await poller.start()

    def _make_send_func(self, devaddr: int):
        """Create a send function with devaddr baked in."""
        async def send(p17_frame: bytes) -> bytes:
            return await self._tcp.send_p17_command(p17_frame, devaddr=devaddr)
        return send

    def _make_result_callback(self, devaddr: int):
        """Create a result callback with devaddr baked in."""
        async def on_result(cmd: str, values: dict[str, Any]) -> None:
            await self._on_poll_result(devaddr, cmd, values)
        return on_result

    async def _on_collector_disconnect(self) -> None:
        """Collector dropped. Stop pollers, restart UDP announcer."""
        logger.warning("Collector disconnected -- stopping pollers, restarting announcer")

        for addr, poller in self._pollers.items():
            await poller.stop()
        self._pollers.clear()

        if self._udp:
            await self._udp.start()

    # ------------------------------------------------------------------
    # Poller result callback
    # ------------------------------------------------------------------

    async def _on_poll_result(self, devaddr: int, cmd: str, values: dict[str, Any]) -> None:
        """Called by poller with parsed sensor data. Push to HA entities."""
        info = self.inverter_info[devaddr]

        # Capture device metadata and update device registry
        if cmd == "GMN" and "model_name" in values:
            info.model_name = str(values["model_name"])
            logger.debug("[addr=%d] Model name: %s", devaddr, info.model_name)
        if cmd == "ID" and "serial_number" in values:
            info.serial_number = str(values["serial_number"])
            logger.debug("[addr=%d] Serial: %s", devaddr, info.serial_number)
        if cmd == "VFW" and "firmware_version" in values:
            info.firmware_version = str(values["firmware_version"])
            logger.debug("[addr=%d] Firmware: %s", devaddr, info.firmware_version)

        if cmd in ("GMN", "ID", "VFW"):
            self._update_device_registry(devaddr)

        # Merge into accumulated data for this inverter
        self.inverter_data[devaddr].update(values)

        # Notify all entities (they filter by their own devaddr)
        self.async_set_updated_data(self.inverter_data)

    # ------------------------------------------------------------------
    # Set commands (for control entities)
    # ------------------------------------------------------------------

    async def async_send_set_command(self, p17_cmd: str, devaddr: int = 1) -> bool:
        """Send a P17 set command string (e.g. 'POP01'). Returns True on ACK."""
        if not self._tcp or not self._tcp.connected:
            logger.error("[addr=%d] Cannot send set command -- no collector connected", devaddr)
            return False

        try:
            frame = build_set(p17_cmd)
            logger.info("[addr=%d] Set TX: cmd=%s frame_hex=%s", devaddr, p17_cmd, frame.hex())
            raw = await self._tcp.send_p17_command(frame, devaddr=devaddr)
            logger.info("[addr=%d] Set RX: cmd=%s raw_hex=%s raw_ascii=%r",
                        devaddr, p17_cmd, raw.hex(),
                        raw.decode("ascii", errors="replace"))
            cmd_type, data = parse_p17(raw)
            logger.info("[addr=%d] Set parsed: cmd=%s type=%s data=%r",
                        devaddr, p17_cmd, cmd_type, data)
            if cmd_type == "A":  # ACK
                logger.info("[addr=%d] Set '%s' acknowledged", devaddr, p17_cmd)
                return True
            logger.warning("[addr=%d] Set '%s' response: %s %s", devaddr, p17_cmd, cmd_type, data)
            return False
        except Exception as e:
            logger.error("[addr=%d] Set '%s' failed: %s", devaddr, p17_cmd, e)
            return False

    async def async_refresh_command(self, cmd: str, devaddr: int = 1) -> None:
        """Force re-poll a command (e.g. after setting a value)."""
        poller = self._pollers.get(devaddr)
        if poller:
            await poller.refresh(cmd)

    # ------------------------------------------------------------------
    # Device info for HA device registry
    # ------------------------------------------------------------------

    def device_info_dict(self, devaddr: int) -> dict[str, Any]:
        """Device info dict for a specific inverter.

        Naming priority:
          1. serial_number (from ID command, unique per inverter)
          2. model_name (from GMN command)
          3. collector_pn (from heartbeat, identifies the logger)
          4. config entry title (fallback)

        Multi-inverter: always append #devaddr.
        """
        info = self.inverter_info.get(devaddr, InverterInfo())

        # Device name: "Inverter" (single) or "Inverter N" (multi)
        if len(self.inverter_addresses) > 1:
            name = f"Inverter {devaddr}"
        else:
            name = "Inverter"

        # Stable identifier: entry_id + devaddr (never changes)
        result: dict[str, Any] = {
            "identifiers": {(DOMAIN, self._entry.entry_id, str(devaddr))},
            "name": name,
            "manufacturer": "Voltronic",
        }
        if info.model_name:
            result["model"] = info.model_name
        if info.firmware_version:
            result["sw_version"] = info.firmware_version
        if info.serial_number:
            result["serial_number"] = info.serial_number

        return result

    def _update_device_registry(self, devaddr: int) -> None:
        """Push current inverter_info into the HA device registry.

        Called after GMN/ID/VFW so device name, model, serial, firmware
        stay current even if the device was deleted and recreated.
        """
        info_dict = self.device_info_dict(devaddr)
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers=info_dict["identifiers"])
        if not device:
            return

        kwargs: dict[str, Any] = {"name": info_dict["name"]}
        if "model" in info_dict:
            kwargs["model"] = info_dict["model"]
        if "sw_version" in info_dict:
            kwargs["sw_version"] = info_dict["sw_version"]
        if "serial_number" in info_dict:
            kwargs["serial_number"] = info_dict["serial_number"]

        registry.async_update_device(device.id, **kwargs)
        logger.debug("[addr=%d] Device registry updated: %s", devaddr, kwargs)
