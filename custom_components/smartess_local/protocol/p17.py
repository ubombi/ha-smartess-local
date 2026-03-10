"""P17 protocol framing (protocol 0x0994) for Voltronic mpp-solar inverters.

Inverter responses come in TWO different framings depending on the command:

  P17 framing (GS, PIRI, MOD, GS2, etc.):
    Poll:     ^P<len_3><cmd><crc_hi><crc_lo><CR>
    Set:      ^S<len_3><cmd><crc_hi><crc_lo><CR>
    Response: ^D<len_3><data><crc_hi><crc_lo><CR>
    ACK:      ^A003<crc_hi><crc_lo><CR>
    NAK:      ^N003<crc_hi><crc_lo><CR>

  Q-protocol framing (ET, FLAG, set ACK/NAK, possibly others):
    Response: (<data><crc_hi><crc_lo><CR>
    ACK:      (ACK<crc_hi><crc_lo><CR>
    NAK:      (NAK<crc_hi><crc_lo><CR>

Length field (P17) = 3 (for the length digits) + len(cmd/data).
CRC-16/XMODEM computed over content bytes.
CRC bytes are stuffed: 0x28 '(' / 0x0D CR / 0x0A LF incremented by 1.
"""

import logging

from custom_components.smartess_local.protocol.crc import crc16_xmodem

logger = logging.getLogger(__name__)

# Bytes that need stuffing in CRC output
_STUFF_BYTES = {0x28, 0x0D, 0x0A}  # '(', CR, LF


def _stuff_crc_byte(b: int) -> int:
    """If CRC byte would conflict with framing, increment by 1."""
    return (b + 1) & 0xFF if b in _STUFF_BYTES else b


def build_poll(cmd: str) -> bytes:
    """Build a P17 poll command frame: ^P<len><cmd><crc><CR>"""
    cmd_bytes = cmd.encode("ascii")
    length = 3 + len(cmd_bytes)
    length_str = f"{length:03d}"

    # Frame before CRC: ^ + P + length + command
    frame = b"\x5E\x50" + length_str.encode("ascii") + cmd_bytes

    crc = crc16_xmodem(frame)
    crc_hi = _stuff_crc_byte((crc >> 8) & 0xFF)
    crc_lo = _stuff_crc_byte(crc & 0xFF)

    return frame + bytes([crc_hi, crc_lo, 0x0D])


def build_set(cmd: str) -> bytes:
    """Build a P17 set command frame: ^S<len><cmd><crc><CR>"""
    cmd_bytes = cmd.encode("ascii")
    length = 3 + len(cmd_bytes)
    length_str = f"{length:03d}"

    frame = b"\x5E\x53" + length_str.encode("ascii") + cmd_bytes

    crc = crc16_xmodem(frame)
    crc_hi = _stuff_crc_byte((crc >> 8) & 0xFF)
    crc_lo = _stuff_crc_byte(crc & 0xFF)

    return frame + bytes([crc_hi, crc_lo, 0x0D])


def parse_response(data: bytes) -> tuple[str, str]:
    """Parse an inverter response frame (P17 or Q-protocol).

    Returns (command_type, response_data_string).
      command_type: 'D' for data, 'A' for ACK, 'N' for NAK.
      response_data_string: decoded ASCII payload.

    Raises ValueError if frame is malformed.
    """
    if len(data) < 3:
        raise ValueError(f"Response too short: {len(data)} bytes, hex={data.hex()}")

    start = data[0]

    # --- Q-protocol framing: starts with '(' (0x28) ---
    if start == 0x28:
        return _parse_q_protocol(data)

    # --- P17 framing: starts with '^' (0x5E) ---
    if start == 0x5E:
        return _parse_p17(data)

    raise ValueError(
        f"Unknown framing: start=0x{start:02X}, len={len(data)}, hex={data.hex()}, "
        f"ascii={data.decode('ascii', errors='replace')!r}"
    )


def _parse_p17(data: bytes) -> tuple[str, str]:
    """Parse P17-framed response.

    Two formats:
      Standard: ^<type><len3><data><crc_hi><crc_lo><CR>  (>= 7 bytes)
      Short ACK/NAK: ^<0|1><crc_hi><crc_lo><CR>         (5 bytes)
        '1' = ACK, '0' = NAK (used for set command responses)
    """
    # Short ACK/NAK: ^<0|1><crc><CR> = 5 bytes
    if len(data) == 5 and data[1] in (0x30, 0x31):  # '0' or '1'
        ack = data[1] == 0x31
        logger.debug("P17 short %s: hex=%s", "ACK" if ack else "NAK", data.hex())
        return ("A" if ack else "N"), ""

    if len(data) < 7:  # minimum standard: ^D003<crc><crc><CR>
        raise ValueError(f"P17 response too short: {len(data)} bytes, hex={data.hex()}")

    cmd_type = chr(data[1])

    try:
        length = int(data[2:5].decode("ascii"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"P17 invalid length field: {data[2:5]!r}, hex={data.hex()}") from e

    data_len = length - 3
    if data_len < 0:
        raise ValueError(f"P17 invalid length: {length}, hex={data.hex()}")

    response_data = data[5 : 5 + data_len].decode("ascii", errors="replace")

    logger.debug("P17 parse: type=%s len=%d data=%r (raw hex=%s)",
                 cmd_type, length, response_data, data.hex())

    return cmd_type, response_data


def _parse_q_protocol(data: bytes) -> tuple[str, str]:
    """Parse Q-protocol response: (<data><crc_hi><crc_lo><CR>

    ACK = (ACK<crc><CR> -> ('A', '')
    NAK = (NAK<crc><CR> -> ('N', '')
    Data = (<payload><crc><CR> -> ('D', payload)
    """
    # Strip trailing CR if present
    if data[-1] == 0x0D:
        content = data[1:-3]   # skip '(', strip 2 CRC bytes + CR
    else:
        content = data[1:-2]   # skip '(', strip 2 CRC bytes only

    text = content.decode("ascii", errors="replace")

    logger.debug("Q-protocol parse: content=%r (raw hex=%s)", text, data.hex())

    if text == "ACK":
        return "A", ""
    if text == "NAK":
        return "N", ""

    return "D", text


def find_p17_frame(data: bytes) -> tuple[int, int] | None:
    """Find a complete P17 or Q-protocol frame in a byte buffer.

    Returns (start_index, end_index) or None if no complete frame found.
    P17: starts with 0x5E (^), ends with 0x0D (CR).
    Q-protocol: starts with 0x28 ((), ends with 0x0D (CR).
    """
    # Try P17 first
    for start_byte in (0x5E, 0x28):
        start = data.find(bytes([start_byte]))
        if start == -1:
            continue
        min_frame = 4 if start_byte == 0x28 else 7
        end = data.find(b"\x0D", start + min_frame - 1)
        if end == -1:
            continue
        return (start, end + 1)
    return None


# Command name constants
CMD_PI = "PI"  # Protocol ID
CMD_GMN = "GMN"  # Model Name
CMD_T = "T"  # Time
CMD_ET = "ET"  # Energy Totals
CMD_ID = "ID"  # Serial/ID
CMD_VFW = "VFW"  # Firmware Version
CMD_PIRI = "PIRI"  # Rating Info (QPIRI)
CMD_GS = "GS"  # General Status (QPIGS)
CMD_GS2 = "GS2"  # General Status 2 (QPIGS2)
CMD_MOD = "MOD"  # Mode (QMOD)
CMD_FWS = "FWS"  # Fault/Warning Status
CMD_FLAG = "FLAG"  # Flags (QFLAG)
CMD_DI = "DI"  # Default Info
CMD_MCHGCR = "MCHGCR"  # Max Charge Current Rating
CMD_MUCHGCR = "MUCHGCR"  # Max Utility Charge Current Rating
