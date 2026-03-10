"""CRC-16 implementations for Voltronic protocol framing (XMODEM) and transport (MODBUS)."""


def _generate_xmodem_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
        table.append(crc)
    return table


_XMODEM_TABLE = _generate_xmodem_table()


def crc16_xmodem(data: bytes, start: int = 0, end: int = -1) -> int:
    """CRC-16/XMODEM (poly 0x1021, init 0x0000). Used for P17 protocol framing."""
    if end == -1:
        end = len(data)
    crc = 0
    for i in range(start, end):
        crc = ((crc << 8) & 0xFFFF) ^ _XMODEM_TABLE[((crc >> 8) ^ data[i]) & 0xFF]
    return crc


def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS (poly 0xA001, init 0xFFFF). Used for EyBond transport layer."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc
