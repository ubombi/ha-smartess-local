"""EyBond ModBus protocol -- TCP transport layer for EyeBond WiFi collector dongles.

Header format (8 bytes, big-endian):
    [tid:2][devcode:2][wire_len:2][devaddr:1][fcode:1]

IMPORTANT: wire_len = total_frame_length - 6 (NOT total length).
    total_frame_length = wire_len + 6

wire_len encoding:
    wire_len = total_frame_length - 6  (length field on wire)

Heartbeat payload is 6 date bytes [Y-2000,M,D,H,Mi,S] + 2-byte interval,
NOT a UTC timestamp.
"""

import struct
import logging
from datetime import datetime, timezone
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# --- Function codes ---
FC_HEARTBEAT = 1
FC_QUERY_COLLECTOR = 2
FC_SET_COLLECTOR = 3
FC_FORWARD2DEVICE = 4
FC_TRIGGER_QUERY_REAL_TIME = 17
FC_SET_DEVICE_REG = 18
FC_TRIGGER_QUERY_HISTORY = 19

# Device code for Voltronic Solar P17 protocol
DEVCODE_SOLAR_P17 = 0x0994

HEADER_SIZE = 8

# Offset between wire length field and total frame length
WIRE_LEN_OFFSET = 6


@dataclass
class EybondHeader:
    tid: int = 0          # Transaction ID (2 bytes)
    devcode: int = 0      # Device code (2 bytes)
    wire_len: int = 0     # Length field as on wire (total_len - 6)
    devaddr: int = 1      # Device address (1 byte)
    fcode: int = 0        # Function code (1 byte)

    @property
    def total_len(self) -> int:
        """Total frame length in bytes (header + payload)."""
        return self.wire_len + WIRE_LEN_OFFSET

    @property
    def payload_len(self) -> int:
        """Payload length (bytes after header)."""
        return self.total_len - HEADER_SIZE


def encode_header(tid: int, devcode: int, total_len: int,
                  devaddr: int, fcode: int) -> bytes:
    """Encode header. total_len is the real frame size; wire_len computed automatically."""
    wire_len = total_len - WIRE_LEN_OFFSET
    return struct.pack(">HHHBB", tid, devcode, wire_len, devaddr, fcode)


def decode_header(data: bytes) -> EybondHeader:
    """Decode 8-byte big-endian header."""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Header too short: {len(data)} < {HEADER_SIZE}")
    tid, devcode, wire_len, devaddr, fcode = struct.unpack(">HHHBB", data[:HEADER_SIZE])
    return EybondHeader(tid=tid, devcode=devcode, wire_len=wire_len,
                        devaddr=devaddr, fcode=fcode)


class TIDCounter:
    """Transaction ID counter, wraps at 0xFFFF."""

    def __init__(self):
        self._tid = 0

    def next(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFF
        return self._tid


def build_heartbeat_request(tid: int, interval: int = 60) -> bytes:
    """Build heartbeat request frame (FC=1). Server -> Collector.

    Payload (8 bytes): [year-2000, month, day, hour, minute, second, interval:2]
    Total frame: 16 bytes.  Wire length field: 10.
    """
    now = datetime.now(timezone.utc)
    payload = bytes([
        (now.year - 2000) & 0xFF,
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second,
    ]) + struct.pack(">H", interval)  # 6 + 2 = 8 bytes

    total_len = HEADER_SIZE + len(payload)  # 16
    hdr = encode_header(tid, 0, total_len, 1, FC_HEARTBEAT)
    return hdr + payload


def parse_heartbeat_response(data: bytes) -> tuple[EybondHeader, str]:
    """Parse heartbeat response. Returns (header, collector_pn).

    Response payload is 14 bytes: collector PN (serial number) as ASCII.
    Total frame: 22 bytes.
    """
    hdr = decode_header(data)
    pn_bytes = data[HEADER_SIZE:HEADER_SIZE + 14]
    pn = pn_bytes.decode("ascii", errors="replace").strip("\x00")
    return hdr, pn


def build_forward2device(tid: int, p17_frame: bytes,
                         devcode: int = DEVCODE_SOLAR_P17,
                         devaddr: int = 1) -> bytes:
    """Wrap a raw P17 frame in Forward2Device (FC=4) for transmission to collector.

    devaddr selects which inverter on the RS485 bus to forward to.
    """
    total_len = HEADER_SIZE + len(p17_frame)
    hdr = encode_header(tid, devcode, total_len, devaddr, FC_FORWARD2DEVICE)
    return hdr + p17_frame


def parse_forward2device_response(data: bytes) -> tuple[EybondHeader, bytes]:
    """Parse FC=4 response. Returns (header, p17_response_bytes)."""
    hdr = decode_header(data)
    payload = data[HEADER_SIZE:hdr.total_len]
    return hdr, payload


def parse_frame(data: bytes) -> tuple[EybondHeader, bytes]:
    """Generic frame parser. Returns (header, payload)."""
    hdr = decode_header(data)
    payload = data[HEADER_SIZE:hdr.total_len]
    return hdr, payload
