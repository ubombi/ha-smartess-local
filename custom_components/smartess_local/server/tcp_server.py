"""
Asyncio TCP server for EyeBond WiFi collector dongles.

Listens on port 8899, manages heartbeat exchange, and provides a
request/response interface for the poller to send P17 commands to
the inverter(s) through the collector.

Supports multiple inverters on one RS485 bus via devaddr parameter.
Uses asyncio.Lock to serialize commands (RS485 is half-duplex).
"""

import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable

from custom_components.smartess_local.protocol.eybond_modbus import (
    HEADER_SIZE, FC_HEARTBEAT, FC_FORWARD2DEVICE,
    EybondHeader, TIDCounter,
    decode_header,
    build_heartbeat_request, parse_heartbeat_response,
    build_forward2device,
)

logger = logging.getLogger(__name__)


class CollectorConnection:
    """Represents a connected EyBond collector."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.collector_pn: str = ""
        self.last_heartbeat: float = 0.0
        self.pn_notified: bool = False
        self._pending: dict[int, asyncio.Future] = {}
        self._tid = TIDCounter()

    @property
    def peername(self) -> str:
        try:
            return str(self.writer.get_extra_info("peername"))
        except Exception:
            return "unknown"


class TCPServer:
    """TCP server on port 8899 for EyeBond collector connections."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8899,
        heartbeat_interval: float = 60.0,
        request_timeout: float = 5.0,
        on_connect: Optional[Callable[[str, str], Awaitable[None]]] = None,
        on_disconnect: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.host = host
        self.port = port
        self.heartbeat_interval = heartbeat_interval
        self.request_timeout = request_timeout
        self.on_connect = on_connect      # callback(collector_pn, remote_ip)
        self.on_disconnect = on_disconnect
        self._conn: Optional[CollectorConnection] = None
        self._server: Optional[asyncio.Server] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._read_task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()  # serialize RS485 commands

    @property
    def connected(self) -> bool:
        return self._conn is not None

    @property
    def collector_pn(self) -> str:
        return self._conn.collector_pn if self._conn else ""

    async def start(self):
        """Start listening for collector connections."""
        self._server = await asyncio.start_server(
            self._handle_connection, self.host, self.port,
        )
        logger.info("TCP server listening on %s:%d", self.host, self.port)

    async def stop(self):
        """Stop the server and close any active connection."""
        logger.debug("TCP server stopping...")
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._read_task:
            self._read_task.cancel()
        if self._conn:
            self._conn.writer.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("TCP server stopped")

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ):
        """Handle a new collector connection."""
        peername = writer.get_extra_info("peername")
        logger.debug("Incoming TCP connection from %s", peername)

        # Close existing connection if any
        if self._conn:
            logger.warning("New connection from %s while existing active -- replacing", peername)
            await self._close_connection()

        conn = CollectorConnection(reader, writer)
        self._conn = conn
        logger.info("Collector connected from %s", conn.peername)

        # Start heartbeat and read loops
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(conn))
        self._read_task = asyncio.create_task(self._read_loop(conn))

        # Wait for read loop to finish (connection closed)
        try:
            await self._read_task
        except asyncio.CancelledError:
            pass
        finally:
            await self._close_connection()

    async def _close_connection(self):
        """Tear down the active collector connection."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._conn:
            try:
                self._conn.writer.close()
            except Exception:
                pass
            # Cancel all pending futures
            pending_count = 0
            for fut in self._conn._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("Collector disconnected"))
                    pending_count += 1
            if pending_count:
                logger.debug("Cancelled %d pending requests", pending_count)
            self._conn = None
            logger.info("Collector disconnected")
            if self.on_disconnect:
                await self.on_disconnect()

    async def _heartbeat_loop(self, conn: CollectorConnection):
        """Send heartbeat requests periodically."""
        try:
            while True:
                tid = conn._tid.next()
                frame = build_heartbeat_request(tid, int(self.heartbeat_interval))
                conn.writer.write(frame)
                await conn.writer.drain()
                logger.debug("Heartbeat TX  TID=%d  interval=%ds  frame=%s",
                             tid, int(self.heartbeat_interval), frame.hex())
                await asyncio.sleep(self.heartbeat_interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Heartbeat loop error: %s", e, exc_info=True)

    async def _read_loop(self, conn: CollectorConnection):
        """Read frames from collector, dispatch by function code."""
        try:
            while True:
                # Read 8-byte header
                header_data = await conn.reader.readexactly(HEADER_SIZE)
                hdr = decode_header(header_data)
                logger.debug("RX header: TID=%d devcode=0x%04X devaddr=%d FC=%d wire_len=%d",
                             hdr.tid, hdr.devcode, hdr.devaddr, hdr.fcode, hdr.wire_len)

                # Read remaining payload
                payload_size = hdr.payload_len
                if payload_size > 0:
                    payload = await conn.reader.readexactly(payload_size)
                elif payload_size == 0:
                    payload = b""
                else:
                    logger.warning("Invalid frame: wire_len=%d payload_size=%d, skipping",
                                   hdr.wire_len, payload_size)
                    continue

                full_frame = header_data + payload
                logger.debug("RX frame (%d bytes): %s", len(full_frame), full_frame.hex())

                # Dispatch by function code
                if hdr.fcode == FC_HEARTBEAT:
                    _, pn = parse_heartbeat_response(full_frame)
                    conn.collector_pn = pn
                    conn.last_heartbeat = time.time()
                    logger.info("Heartbeat RX -- collector PN: %s  devcode=0x%04X devaddr=0x%02X",
                                pn, hdr.devcode, hdr.devaddr)
                    # Fire on_connect callback once (as task, not awaited --
                    # the callback may call send_p17_command which needs
                    # this read loop to be running to receive responses)
                    if self.on_connect and pn and not conn.pn_notified:
                        conn.pn_notified = True
                        peername_info = conn.writer.get_extra_info("peername")
                        remote_ip = peername_info[0] if peername_info else "unknown"
                        logger.debug("First heartbeat -- scheduling on_connect(%s, %s)", pn, remote_ip)
                        asyncio.create_task(self.on_connect(pn, remote_ip))

                elif hdr.fcode == FC_FORWARD2DEVICE:
                    logger.debug("RX FC=4 response TID=%d devaddr=%d payload(%d)=%s",
                                 hdr.tid, hdr.devaddr, len(payload), payload.hex())
                    fut = conn._pending.pop(hdr.tid, None)
                    if fut and not fut.done():
                        fut.set_result(payload)
                    else:
                        logger.debug("Unsolicited/late FC=4 TID=%d (pending TIDs: %s)",
                                     hdr.tid, list(conn._pending.keys()))

                else:
                    logger.debug("Unhandled FC=%d TID=%d devaddr=%d len=%d payload=%s",
                                 hdr.fcode, hdr.tid, hdr.devaddr, hdr.total_len, payload.hex())

        except asyncio.IncompleteReadError:
            logger.info("Collector connection closed (incomplete read)")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Read loop error: %s", e, exc_info=True)

    async def send_p17_command(self, p17_frame: bytes, devaddr: int = 1) -> bytes:
        """Send a P17 command to an inverter via the collector.

        Args:
            p17_frame: Complete P17 frame (from p17.build_poll or build_set).
            devaddr: RS485 device address (1-based, each inverter has unique addr).

        Returns:
            Raw P17 response bytes (the payload inside FC=4 response).

        Raises:
            ConnectionError: No collector connected.
            asyncio.TimeoutError: Response not received within timeout.
        """
        if not self._conn:
            raise ConnectionError("No collector connected")

        async with self._send_lock:
            conn = self._conn
            if not conn:
                raise ConnectionError("Collector disconnected during lock wait")

            tid = conn._tid.next()
            frame = build_forward2device(tid, p17_frame, devaddr=devaddr)

            # Create future for response
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bytes] = loop.create_future()
            conn._pending[tid] = fut

            try:
                conn.writer.write(frame)
                await conn.writer.drain()
                logger.debug("TX FC=4 TID=%d devaddr=%d frame(%d)=%s",
                             tid, devaddr, len(frame), frame.hex())

                result = await asyncio.wait_for(fut, timeout=self.request_timeout)
                logger.debug("RX response for TID=%d (%d bytes): %s",
                             tid, len(result), result.hex())
                return result
            except asyncio.TimeoutError:
                conn._pending.pop(tid, None)
                logger.warning("Timeout waiting for TID=%d devaddr=%d (%.1fs)",
                               tid, devaddr, self.request_timeout)
                raise
            except Exception:
                conn._pending.pop(tid, None)
                raise
