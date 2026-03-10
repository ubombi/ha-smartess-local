"""Constants for the SmartESS Local integration."""

DOMAIN = "smartess_local"

# --- Config entry data keys ---
CONF_SERVER_IP = "server_ip"
CONF_TCP_PORT = "tcp_port"
CONF_UDP_PORT = "udp_port"
CONF_UDP_BROADCAST_IP = "udp_broadcast_ip"
CONF_HEARTBEAT_INTERVAL = "heartbeat_interval"
CONF_INVERTER_COUNT = "inverter_count"

# --- Options keys (poll intervals, per config entry) ---
CONF_POLL_FAST = "poll_fast"
CONF_POLL_MEDIUM = "poll_medium"
CONF_POLL_SLOW = "poll_slow"

# --- Defaults ---
DEFAULT_TCP_PORT = 8899
DEFAULT_UDP_PORT = 58899
DEFAULT_UDP_BROADCAST_IP = "255.255.255.255"
DEFAULT_HEARTBEAT_INTERVAL = 60
DEFAULT_INVERTER_COUNT = 1
DEFAULT_POLL_FAST = 5       # GS, GS2, MOD
DEFAULT_POLL_MEDIUM = 10    # FWS
DEFAULT_POLL_SLOW = 60      # ET, FLAG

# Grouped-to-per-command mapping
FAST_COMMANDS = ["GS", "GS2", "MOD"]
MEDIUM_COMMANDS = ["FWS"]
SLOW_COMMANDS = ["ET", "FLAG"]
STARTUP_COMMANDS = ["PIRI", "PI", "GMN", "ID", "VFW"]


def build_poll_intervals(
    fast: int = DEFAULT_POLL_FAST,
    medium: int = DEFAULT_POLL_MEDIUM,
    slow: int = DEFAULT_POLL_SLOW,
) -> dict[str, int]:
    """Build per-command intervals from grouped values."""
    intervals: dict[str, int] = {}
    for cmd in FAST_COMMANDS:
        intervals[cmd] = fast
    for cmd in MEDIUM_COMMANDS:
        intervals[cmd] = medium
    for cmd in SLOW_COMMANDS:
        intervals[cmd] = slow
    for cmd in STARTUP_COMMANDS:
        intervals[cmd] = -1  # once at startup
    return intervals


PLATFORMS: list[str] = ["sensor", "select", "number", "switch"]
