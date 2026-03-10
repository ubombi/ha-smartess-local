"""Coordinator for SmartESS Local integration.

Owns the full lifecycle: TCP server, UDP announcer, one poller per inverter.
Supports multiple inverters on a single collector's RS485 bus.
Provides data to HA entities via DataUpdateCoordinator push pattern.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.smartess_local.const import (
    DOMAIN,
    PLATFORMS,
    CONF_SERVER_IP,
    CONF_TCP_PORT,
    CONF_UDP_PORT,
    CONF_UDP_BROADCAST_IP,
    CONF_HEARTBEAT_INTERVAL,
    CONF_POLL_FAST,
    CONF_POLL_MEDIUM,
    CONF_POLL_SLOW,
    MAX_INVERTER_ADDRESS,
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    DEFAULT_UDP_BROADCAST_IP,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_POLL_FAST,
    DEFAULT_POLL_MEDIUM,
    DEFAULT_POLL_SLOW,
    build_poll_intervals,
)
from custom_components.smartess_local.server.tcp_server import TCPServer
from custom_components.smartess_local.server.udp_announcer import UDPAnnouncer
from custom_components.smartess_local.inverter.poller import InverterPoller
from custom_components.smartess_local.protocol.p17 import build_poll, build_set, parse_response as parse_p17
from custom_components.smartess_local.inverter.sensors import parse_response as parse_sensor_response

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


def _arp_lookup(ip: str) -> str | None:
    """Look up MAC address from the system ARP cache. Linux only."""
    try:
        with open("/proc/net/arp") as f:
            for line in f:
                if line.startswith(ip + " "):
                    mac = line.split()[3]
                    if mac and mac != "00:00:00:00:00:00":
                        return mac
    except OSError:
        pass
    return None


@dataclass
class InverterInfo:
    """Per-inverter metadata, populated from startup commands."""
    model_name: str = ""
    serial_number: str = ""
    firmware_version: str = ""
    power_rating: int = 0       # W, from PIRI ac_output_active_power_rating
    voltage_rating: float = 0.0 # V, from PIRI battery_voltage_rating


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
        self.collector_ip: str = ""
        self.collector_mac: str | None = None

        # Per-inverter data: devaddr -> {sensor_name: value}
        self.inverter_data: dict[int, dict[str, Any]] = {}
        # Per-inverter metadata
        self.inverter_info: dict[int, InverterInfo] = {}

        # Inverter addresses (1-based), populated by _discover_inverters()
        self.inverter_addresses: list[int] = []
        self._platforms_loaded = False

        logger.debug("Coordinator init: entry_id=%s (discovery pending)", entry.entry_id)

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

        logger.info("Setting up SmartESS Local coordinator: server=%s:%d udp=%d (discovery pending)",
                     server_ip, tcp_port, udp_port)

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

    async def _on_collector_connect(self, collector_pn: str, remote_ip: str) -> None:
        """First heartbeat received -- collector identified. Discover inverters, start polling."""
        self.collector_pn = collector_pn
        self.collector_ip = remote_ip
        self.collector_mac = _arp_lookup(remote_ip)
        logger.info("Collector identified: %s (ip=%s mac=%s) -- scanning RS485 bus",
                     collector_pn, remote_ip, self.collector_mac or "unknown")

        # Update config entry title to show logger PN and IP
        if collector_pn:
            title = f"Logger {collector_pn} ({remote_ip})" if remote_ip else f"Logger {collector_pn}"
            self.hass.config_entries.async_update_entry(self._entry, title=title)

        # Stop UDP announcer (collector is here)
        if self._udp:
            await self._udp.stop()

        # Let collector settle after heartbeat handshake before sending P17 commands
        await asyncio.sleep(2.0)

        # Discover inverters on the RS485 bus (retry on transient failure)
        for attempt in range(1, 4):
            await self._discover_inverters()
            if self.inverter_addresses:
                break
            delay = 2 * attempt
            logger.warning("No inverters found (attempt %d/3) -- retrying in %ds", attempt, delay)
            await asyncio.sleep(delay)

        if not self.inverter_addresses:
            logger.error("No inverters found on RS485 bus after 3 attempts (scanned 1..%d)",
                         MAX_INVERTER_ADDRESS)
            return

        # Register logger device (parent for all inverters on this bus)
        self._register_logger_device()

        # Load entity platforms (sensor/select/number/switch) now that we know the addresses
        if not self._platforms_loaded:
            self._platforms_loaded = True
            await self.hass.config_entries.async_forward_entry_setups(self._entry, PLATFORMS)

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

    # ------------------------------------------------------------------
    # RS485 bus discovery
    # ------------------------------------------------------------------

    async def _discover_inverters(self) -> None:
        """Probe RS485 addresses 1..MAX_INVERTER_ADDRESS with PI command.

        Addresses are contiguous starting from 1, so we stop at the first
        address that NAKs or times out.  Also queries ID to get serial number
        for each discovered inverter (needed for device naming before entities
        are created).
        """
        discovered: list[int] = []
        for addr in range(1, MAX_INVERTER_ADDRESS + 1):
            try:
                frame = build_poll("PI")
                raw = await self._tcp.send_p17_command(frame, devaddr=addr)
                cmd_type, _data = parse_p17(raw)
                if cmd_type == "N":  # NAK
                    logger.debug("Address %d NAK'd PI -- end of bus", addr)
                    break
                # Valid response (D or A) = inverter present
                discovered.append(addr)
                logger.debug("Address %d responded to PI: type=%s", addr, cmd_type)
            except asyncio.TimeoutError:
                logger.debug("Address %d timed out -- end of bus", addr)
                break
            except ConnectionError:
                logger.warning("Collector disconnected during discovery")
                break
            except Exception as e:
                logger.warning("Address %d probe error: %s -- skipping", addr, e)
                break

        # Query serial for each discovered address, deduplicate by serial.
        # Some collectors forward to the same inverter regardless of devaddr,
        # causing all 16 addresses to echo the same response. Stop as soon
        # as we see a duplicate serial (addresses are contiguous).
        seen_serials: set[str] = set()
        unique: list[int] = []
        for addr in discovered:
            if addr not in self.inverter_data:
                self.inverter_data[addr] = {}
            if addr not in self.inverter_info:
                self.inverter_info[addr] = InverterInfo()
            await self._query_serial(addr)
            sn = self.inverter_info[addr].serial_number
            if sn and sn in seen_serials:
                logger.debug("Address %d has duplicate serial %s -- end of unique inverters", addr, sn)
                break
            if sn:
                seen_serials.add(sn)
            unique.append(addr)

        if len(unique) < len(discovered):
            logger.info("Deduplicated %d -> %d addresses (collector echoes all devaddrs)",
                        len(discovered), len(unique))

        self.inverter_addresses = unique
        serials = {a: self.inverter_info[a].serial_number for a in unique}
        logger.info("Found %d inverter(s) on RS485 bus: %s", len(unique), serials)

    async def _query_serial(self, devaddr: int) -> None:
        """Query ID command to get serial number for a single inverter."""
        try:
            frame = build_poll("ID")
            raw = await self._tcp.send_p17_command(frame, devaddr=devaddr)
            cmd_type, response_data = parse_p17(raw)
            if cmd_type == "N":
                logger.debug("[addr=%d] ID NAK'd during discovery", devaddr)
                return
            values = parse_sensor_response("ID", response_data)
            sn = values.get("serial_number", "")
            if sn:
                self.inverter_info[devaddr].serial_number = str(sn)
                logger.debug("[addr=%d] Serial: %s", devaddr, sn)
        except Exception as e:
            logger.warning("[addr=%d] ID query failed during discovery: %s", devaddr, e)

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
        if cmd == "PIRI":
            if "ac_output_active_power_rating" in values:
                info.power_rating = int(values["ac_output_active_power_rating"])
            if "battery_voltage_rating" in values:
                info.voltage_rating = float(values["battery_voltage_rating"])
            if info.power_rating or info.voltage_rating:
                logger.debug("[addr=%d] Ratings: %dW / %.0fV", devaddr, info.power_rating, info.voltage_rating)

        if cmd in ("GMN", "ID", "VFW", "PIRI"):
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

    def logger_device_info(self) -> dict[str, Any]:
        """Device info dict for the collector (logger) dongle.

        Acts as a parent device for all inverters on its RS485 bus.
        """
        pn = self.collector_pn or "unknown"
        name = f"Logger {pn} ({self.collector_ip})" if self.collector_ip else f"Logger {pn}"
        info: dict[str, Any] = {
            "identifiers": {(DOMAIN, f"{self._entry.entry_id}_logger")},
            "name": name,
            "manufacturer": "EyeBond",
            "model": "WiFi Collector",
        }
        if self.collector_mac:
            info["connections"] = {(dr.CONNECTION_NETWORK_MAC, self.collector_mac)}
        return info

    def device_info_dict(self, devaddr: int) -> dict[str, Any]:
        """Device info dict for a specific inverter.

        Name uses serial number when available (queried during discovery),
        falls back to collector PN + address.
        Entity IDs derive from this name: sensor.inverter_{serial}_grid_voltage
        """
        info = self.inverter_info.get(devaddr, InverterInfo())

        if info.serial_number:
            name = f"Inverter {info.serial_number}"
        elif self.collector_pn:
            name = f"Inverter {self.collector_pn} {devaddr}"
        else:
            name = f"Inverter {devaddr}"

        # Stable identifier: entry_id + devaddr (never changes)
        result: dict[str, Any] = {
            "identifiers": {(DOMAIN, f"{self._entry.entry_id}_{devaddr}")},
            "name": name,
            "manufacturer": "Voltronic (probably)",
            "via_device": (DOMAIN, f"{self._entry.entry_id}_logger"),
        }
        if info.model_name:
            model = info.model_name
            # Append ratings from PIRI if available: "07 - 5000W/48V"
            if info.power_rating or info.voltage_rating:
                specs = []
                if info.power_rating:
                    specs.append(f"{info.power_rating}W")
                if info.voltage_rating:
                    specs.append(f"{info.voltage_rating:g}V")
                model = f"{model} - {'/'.join(specs)}"
            result["model"] = model
        if info.firmware_version:
            result["sw_version"] = info.firmware_version
        if info.serial_number:
            result["serial_number"] = info.serial_number

        return result

    def _register_logger_device(self) -> None:
        """Create the logger (collector dongle) device in the registry.

        This device has no entities -- it exists as a parent for inverter devices.
        """
        info = self.logger_device_info()
        registry = dr.async_get(self.hass)
        kwargs: dict[str, Any] = {
            "config_entry_id": self._entry.entry_id,
            "identifiers": info["identifiers"],
            "name": info["name"],
            "manufacturer": info.get("manufacturer"),
            "model": info.get("model"),
        }
        if "connections" in info:
            kwargs["connections"] = info["connections"]
        registry.async_get_or_create(**kwargs)
        logger.debug("Logger device registered: %s (mac=%s)", info["name"], self.collector_mac)

    def _update_device_registry(self, devaddr: int) -> None:
        """Push current inverter_info into the HA device registry.

        Called after GMN/ID/VFW/PIRI so device name, model, serial, firmware
        stay current even if the device was deleted and recreated.
        Does not overwrite user-customized name (name_by_user).
        """
        info_dict = self.device_info_dict(devaddr)
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers=info_dict["identifiers"])
        if not device:
            return

        kwargs: dict[str, Any] = {}
        # Only update integration name; name_by_user (user rename) is untouched
        if not device.name_by_user:
            kwargs["name"] = info_dict["name"]
        if "model" in info_dict:
            kwargs["model"] = info_dict["model"]
        if "sw_version" in info_dict:
            kwargs["sw_version"] = info_dict["sw_version"]
        if "serial_number" in info_dict:
            kwargs["serial_number"] = info_dict["serial_number"]

        if kwargs:
            registry.async_update_device(device.id, **kwargs)
            logger.debug("[addr=%d] Device registry updated: %s", devaddr, kwargs)
