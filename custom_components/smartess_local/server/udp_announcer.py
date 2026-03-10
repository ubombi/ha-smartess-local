"""Broadcasts UDP packets to redirect the EyeBond WiFi collector dongle to local server.

The EyeBond collector listens on UDP port 58899. When it receives a packet
with the format ``set>server=<IP>:<PORT>;``, it initiates a TCP connection
to that IP:PORT instead of the Chinese cloud.
"""

import asyncio
import logging
import socket

logger = logging.getLogger(__name__)


class UDPAnnouncer:
    """Broadcasts UDP discovery packets to redirect EyeBond collector to local server."""

    def __init__(
        self,
        server_ip: str,
        server_port: int = 8899,
        broadcast_ip: str = "255.255.255.255",
        udp_port: int = 58899,
        interval: float = 5.0,
    ):
        self.server_ip = server_ip
        self.server_port = server_port
        self.broadcast_ip = broadcast_ip
        self.udp_port = udp_port
        self.interval = interval
        self._running = False
        self._task: asyncio.Task | None = None

    def _build_payload(self) -> bytes:
        return f"set>server={self.server_ip}:{self.server_port};".encode("ascii")

    async def start(self):
        """Start broadcasting. Call stop() or set connected=True to stop."""
        self._running = True
        self._task = asyncio.create_task(self._broadcast_loop())
        logger.info("UDP announcer started on %s:%d", self.broadcast_ip, self.udp_port)

    async def stop(self):
        """Stop broadcasting."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("UDP announcer stopped")

    async def _broadcast_loop(self):
        # Create a UDP socket with broadcast enabled
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)

        payload = self._build_payload()
        logger.info("Broadcasting: %s", payload.decode())

        try:
            while self._running:
                try:
                    sock.sendto(payload, (self.broadcast_ip, self.udp_port))
                    logger.debug("UDP announce sent to %s:%d", self.broadcast_ip, self.udp_port)
                except OSError as e:
                    logger.warning("UDP send failed: %s", e)
                await asyncio.sleep(self.interval)
        finally:
            sock.close()
