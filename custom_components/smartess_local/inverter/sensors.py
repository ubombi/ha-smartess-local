"""Sensor definitions for SmartESS Local mpp-solar inverters (protocol P17 / 0x0994).

Each P17 command returns COMMA-SEPARATED scaled integer values.
Values are raw integers; scaling (e.g. x0.1 for voltages) is applied here.

Verified against real inverter output via EyeBond collector.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass
class SensorDef:
    """Single sensor field within a P17 command response."""

    name: str           # snake_case key, used for data storage and unique_id
    index: int
    unit: Optional[str] = None
    device_class: Optional[str] = None
    state_class: Optional[str] = None
    value_type: type = int
    scale: float = 1.0
    icon: Optional[str] = None
    enabled_default: bool = True  # False = disabled in HA UI by default
    label: str = ""     # Human-friendly display name; if empty, auto-generated from name


# ---------------------------------------------------------------------------
# GS -- general status (28 comma-separated integer fields)
# ---------------------------------------------------------------------------

# Field order verified against real P17 GS response from EyeBond collector.
# NOTE: Differs from standard QPIGS! Fields 7-15 are rearranged vs ESPHome/pipsolar.
# Indices 8-11, 14-15 are AMBIGUOUS (all zero at night) -- need daytime PV data to confirm.
_GS_SENSORS = [
    SensorDef("grid_voltage",             0,  "V",  "voltage",        "measurement", scale=0.1),
    SensorDef("grid_frequency",           1,  "Hz", "frequency",      "measurement", scale=0.1),
    SensorDef("ac_output_voltage",        2,  "V",  "voltage",        "measurement", scale=0.1,
              label="AC Output Voltage"),
    SensorDef("ac_output_frequency",      3,  "Hz", "frequency",      "measurement", scale=0.1,
              label="AC Output Frequency"),
    SensorDef("ac_output_apparent_power", 4,  "VA", "apparent_power", "measurement",
              label="AC Output Apparent Power"),
    SensorDef("ac_output_active_power",   5,  "W",  "power",          "measurement",
              label="AC Output Active Power"),
    SensorDef("output_load_percent",      6,  "%",  None,             "measurement", icon="mdi:percent"),
    # --- CONFIRMED: 54.4V = battery voltage for 48V system at float ---
    SensorDef("battery_voltage",          7,  "V",  "voltage",        "measurement", scale=0.1,
              icon="mdi:battery"),
    # --- AMBIGUOUS: all zero at night, need daytime data ---
    SensorDef("battery_voltage_from_scc", 8,  "V",  "voltage",        "measurement", scale=0.1,
              label="Battery Voltage From SCC"),  # GUESS -- 0V at night plausible
    SensorDef("battery_charging_current", 9,  "A",  "current",        "measurement",
              icon="mdi:current-dc"),  # GUESS -- 0A at night plausible
    SensorDef("battery_discharge_current",10, "A",  "current",        "measurement",
              icon="mdi:current-dc"),  # GUESS -- 0A on grid plausible
    SensorDef("gs_field_11",              11, value_type=int,
              enabled_default=False),  # UNKNOWN -- always 0? bus_voltage? pv_current?
    # --- CONFIRMED: raw 100 = 100% battery, NO ×0.1 scale ---
    SensorDef("battery_capacity",         12, "%",  "battery",        "measurement",
              icon="mdi:battery"),
    # --- CONFIRMED: raw 032 = 32°C, NO ×0.1 scale ---
    SensorDef("inverter_heat_sink_temp",  13, "°C", "temperature",    "measurement",
              icon="mdi:thermometer"),
    # --- Fields 14-15: always zero on 0994, PV data at 16/18 instead ---
    # --- Fields 16-18: CONFIRMED PV1 power and voltage (verified against SmartESS app) ---
    SensorDef("pv1_input_power",          16, "W",  "power",          "measurement",
              icon="mdi:solar-power", label="PV1 Input Power"),
    SensorDef("pv1_input_voltage",        18, "V",  "voltage",        "measurement", scale=0.1,
              icon="mdi:solar-power", label="PV1 Input Voltage"),
    SensorDef("pv2_input_power",          19, "W",  "power",          "measurement",
              icon="mdi:solar-power", label="PV2 Input Power",
              enabled_default=False),  # 0 on single-MPPT systems
    SensorDef("device_status2",           20, value_type=str, icon="mdi:information-outline",
              enabled_default=False),
    SensorDef("status_field_21",          21, value_type=int, enabled_default=False),
    SensorDef("status_field_22",          22, value_type=int, enabled_default=False),
    SensorDef("status_field_23",          23, value_type=int, enabled_default=False),
    SensorDef("status_field_24",          24, value_type=int, enabled_default=False),
    SensorDef("status_field_25",          25, value_type=int, enabled_default=False),
    SensorDef("status_field_26",          26, value_type=int, enabled_default=False),
    SensorDef("status_field_27",          27, value_type=int, enabled_default=False),
]

# ---------------------------------------------------------------------------
# GS2 -- second PV input (3 fields)
# ---------------------------------------------------------------------------

_GS2_SENSORS = [
    SensorDef("pv2_input_voltage",   1, "V", "voltage", "measurement", scale=0.1,
              icon="mdi:solar-power", label="PV2 Input Voltage"),
    SensorDef("pv2_charging_power",  2, "W", "power",   "measurement",
              icon="mdi:solar-power", label="PV2 Charging Power"),
]

# ---------------------------------------------------------------------------
# PIRI -- rated / config information
# ---------------------------------------------------------------------------

_PIRI_SENSORS = [
    # Verified against real PIRI response (26 fields, P17 protocol 0x0994).
    # Two extra fields vs standard QPIRI: ac_output_frequency (3), battery_redischarge_voltage (10).
    SensorDef("ac_input_voltage_rating",         0,  "V",  "voltage", scale=0.1,
              enabled_default=False, label="AC Input Voltage Rating"),
    SensorDef("ac_input_current_rating",         1,  "A",  "current", scale=0.1,
              enabled_default=False, label="AC Input Current Rating"),
    SensorDef("ac_output_voltage_rating",        2,  "V",  "voltage", scale=0.1,
              enabled_default=False, label="AC Output Voltage Rating"),
    SensorDef("ac_output_frequency_rating",      3,  "Hz", "frequency", scale=0.1,
              enabled_default=False, label="AC Output Frequency Rating"),
    SensorDef("ac_output_current_rating",        4,  "A",  "current", scale=0.1,
              enabled_default=False, label="AC Output Current Rating"),
    SensorDef("ac_output_apparent_power_rating", 5,  "VA", "apparent_power",
              enabled_default=False, label="AC Output Apparent Power Rating"),
    SensorDef("ac_output_active_power_rating",   6,  "W",  "power",
              enabled_default=False, label="AC Output Active Power Rating"),
    SensorDef("battery_voltage_rating",          7,  "V",  "voltage", scale=0.1,
              enabled_default=False),
    SensorDef("battery_recharge_voltage",        8,  "V",  "voltage", scale=0.1,
              enabled_default=False),
    SensorDef("battery_under_voltage",           9,  "V",  "voltage", scale=0.1,
              enabled_default=False),
    SensorDef("battery_redischarge_voltage",     10, "V",  "voltage", scale=0.1,
              enabled_default=False),
    SensorDef("battery_bulk_voltage",            11, "V",  "voltage", scale=0.1,
              enabled_default=False),
    SensorDef("battery_float_voltage",           12, "V",  "voltage", scale=0.1,
              enabled_default=False),
    SensorDef("battery_type",                    13, value_type=int,
              icon="mdi:battery-outline", enabled_default=False),
    SensorDef("max_ac_charging_current",         14, "A",  "current",
              enabled_default=False, label="Max AC Charging Current"),
    SensorDef("max_charging_current",            15, "A",  "current",
              enabled_default=False),
    SensorDef("input_voltage_range",             16, value_type=int,
              enabled_default=False),
    SensorDef("output_source_priority",          17, value_type=int,
              enabled_default=False),
    SensorDef("charger_source_priority",         18, value_type=int,
              enabled_default=False),
    SensorDef("parallel_max_num",                19, value_type=int,
              enabled_default=False),
    SensorDef("machine_type",                    20, value_type=int,
              enabled_default=False),
    SensorDef("topology",                        21, value_type=int,
              enabled_default=False),
    SensorDef("output_mode",                     22, value_type=int,
              enabled_default=False),
    SensorDef("pv_ok_condition",                 23, value_type=int,
              enabled_default=False, label="PV OK Condition"),
    SensorDef("pv_power_balance",                24, value_type=int,
              enabled_default=False, label="PV Power Balance"),
    SensorDef("max_charging_time_at_cv",         25, "s",  value_type=int,
              icon="mdi:timer-outline", enabled_default=False, label="Max Charging Time At CV"),
]

# ---------------------------------------------------------------------------
# MOD -- operating mode
# ---------------------------------------------------------------------------

MODE_MAP: dict[str, str] = {
    "00": "Power on",
    "01": "Standby",
    "02": "Line",
    "03": "Battery",
    "04": "Fault",
    "05": "Hybrid",
    "06": "Shutdown",
    "P": "Power on",
    "S": "Standby",
    "L": "Line",
    "B": "Battery",
    "F": "Fault",
    "H": "Power saving",
    "D": "Shutdown",
}

_MOD_SENSORS = [
    SensorDef("operating_mode", 0, value_type=str, icon="mdi:state-machine"),
]

# ---------------------------------------------------------------------------
# Battery type map (PIRI field 12)
# ---------------------------------------------------------------------------

BATTERY_TYPE_MAP: dict[int, str] = {
    0: "AGM",
    1: "Flooded",
    2: "User-defined",
    3: "Pylontech",
    4: "Weco",
    5: "Soltaro",
    6: "BAK",
    7: "Lithium (LIB)",
    8: "Lithium Iron (LIC)",
    9: "Lithium (LIB2)",
}

# ---------------------------------------------------------------------------
# FWS -- fault / warning status
# ---------------------------------------------------------------------------

FAULT_CODES: dict[int, str] = {
    0:  "No fault",
    1:  "Fan locked",
    2:  "Over temperature",
    3:  "Battery voltage too high",
    4:  "Battery voltage too low",
    5:  "Output short / over temp",
    6:  "Output voltage too high",
    7:  "Overload timeout",
    8:  "Bus voltage too high",
    9:  "Bus soft start failed",
    10: "PV over current",
    11: "PV over voltage",
    12: "DC over current",
    13: "Battery discharge over current",
    51: "Over current inverter",
    52: "Bus soft start failed (2)",
    53: "Inverter soft start failed",
    54: "Self-test failed",
    55: "Over DC voltage on output",
    56: "Battery connection open",
    57: "Current sensor failed",
    58: "Output voltage too low",
}

WARNING_CODES: dict[int, str] = {
    0:  "No warning",
    1:  "Battery low",
    2:  "Over temperature",
    3:  "Overload",
    4:  "Fan warning",
    5:  "Battery under voltage",
    6:  "Battery open",
    7:  "Mains abnormal",
    8:  "Line fail",
    9:  "PV voltage high",
    10: "PV under voltage",
}

_FWS_SENSORS = [
    SensorDef("fault_code",   0, value_type=int, icon="mdi:alert-circle"),
    SensorDef("warning_code", 1, value_type=int, icon="mdi:alert"),
]

# ---------------------------------------------------------------------------
# ET -- energy total
# ---------------------------------------------------------------------------

_ET_SENSORS = [
    SensorDef("total_energy", 0, "kWh", "energy", "total_increasing",
              icon="mdi:lightning-bolt"),
]

# ---------------------------------------------------------------------------
# FLAG -- comma-separated 0/1 values (10 fields on 0994 firmware)
# Flag indices (0x0994 firmware, PI17 protocol):
#   0: buzzer (a)
#   1: overload_bypass (b)
#   2: lcd_return_to_default (c)
#   3: overload_restart (d)
#   4: temperature_restart (e)
#   5: backlight (f)
#   6: primary_source_alarm (g)
#   7: fault_code_record (h)
#   8: grid_frequency_power (i)
#   9: battery_immediate_turnon (j)
# We store individual flags AND the raw string for diagnostics.
# ---------------------------------------------------------------------------

_FLAG_SENSORS = [
    SensorDef("flag_buzzer",                0, value_type=int, enabled_default=False),
    SensorDef("flag_overload_bypass",       1, value_type=int, enabled_default=False),
    SensorDef("flag_lcd_return_to_default", 2, value_type=int, enabled_default=False),
    SensorDef("flag_overload_restart",      3, value_type=int, enabled_default=False),
    SensorDef("flag_temperature_restart",   4, value_type=int, enabled_default=False),
    SensorDef("flag_backlight",             5, value_type=int, enabled_default=False),
    SensorDef("flag_primary_source_alarm",  6, value_type=int, enabled_default=False),
    SensorDef("flag_fault_code_record",     7, value_type=int, enabled_default=False),
    SensorDef("flag_grid_frequency_power",  8, value_type=int, enabled_default=False),
    SensorDef("flag_battery_immediate_turnon", 9, value_type=int, enabled_default=False),
]

# ---------------------------------------------------------------------------
# Static / identification commands
# ---------------------------------------------------------------------------

_PI_SENSORS  = [SensorDef("protocol_id",      0, value_type=str, enabled_default=False)]
_GMN_SENSORS = [SensorDef("model_name",        0, value_type=str, icon="mdi:information")]
_ID_SENSORS  = [SensorDef("serial_number",     0, value_type=str, icon="mdi:identifier")]
_VFW_SENSORS = [SensorDef("firmware_version",  0, value_type=str, icon="mdi:chip")]


# ---------------------------------------------------------------------------
# Top-level sensor map
# ---------------------------------------------------------------------------

SENSOR_MAP: dict[str, list[SensorDef]] = {
    "GS":   _GS_SENSORS,
    "GS2":  _GS2_SENSORS,
    "PIRI": _PIRI_SENSORS,
    "MOD":  _MOD_SENSORS,
    "FWS":  _FWS_SENSORS,
    "ET":   _ET_SENSORS,
    "FLAG": _FLAG_SENSORS,
    "PI":   _PI_SENSORS,
    "GMN":  _GMN_SENSORS,
    "ID":   _ID_SENSORS,
    "VFW":  _VFW_SENSORS,
}


# ---------------------------------------------------------------------------
# P17 length-prefixed string helper
# ---------------------------------------------------------------------------

# Some P17 string responses (ID, GMN, VFW) use a length-prefixed format:
#   <2-digit length><string><zero padding>
# e.g. "1496132212101133000000" → length=14, string="96132212101133"
_LENGTH_PREFIXED_COMMANDS = {"ID", "GMN", "VFW"}


def _decode_length_prefixed(raw: str) -> str:
    """Decode a P17 length-prefixed string field.

    Format: <NN><string of length NN><optional zero padding>
    Falls back to stripping trailing zeros if length prefix is invalid.
    """
    if len(raw) < 3:
        return raw.rstrip("0") or raw
    try:
        str_len = int(raw[:2])
        if 0 < str_len <= len(raw) - 2:
            return raw[2:2 + str_len]
    except (ValueError, TypeError):
        pass
    # Fallback: strip trailing zeros
    return raw.rstrip("0") or raw


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def parse_response(cmd: str, raw: str) -> dict[str, Any]:
    """Parse a comma-separated P17 response into {sensor_name: typed_value}."""
    sensors = SENSOR_MAP.get(cmd)
    if sensors is None:
        log.warning("Unknown command %r -- no sensor mapping", cmd)
        return {}

    if "," in raw:
        fields = raw.strip().split(",")
    else:
        fields = [raw.strip()]

    log.debug("[%s] raw=%r  fields(%d)=%s", cmd, raw, len(fields), fields)

    expected = max(s.index for s in sensors) + 1 if sensors else 0
    if len(fields) < expected:
        log.warning("[%s] expected >= %d fields, got %d: %s", cmd, expected, len(fields), fields)

    result: dict[str, Any] = {}
    for sensor in sensors:
        if sensor.index >= len(fields):
            log.debug("[%s] field %d (%s) missing (only %d fields)",
                      cmd, sensor.index, sensor.name, len(fields))
            continue

        raw_val = fields[sensor.index].strip()

        if sensor.value_type is str:
            if cmd in _LENGTH_PREFIXED_COMMANDS:
                decoded = _decode_length_prefixed(raw_val)
                log.debug("[%s] %s [%d] = %r -> %r (length-prefixed str)",
                          cmd, sensor.name, sensor.index, raw_val, decoded)
                result[sensor.name] = decoded
            else:
                result[sensor.name] = raw_val
                log.debug("[%s] %s [%d] = %r (str)", cmd, sensor.name, sensor.index, raw_val)
        else:
            try:
                parsed = int(raw_val)
                if sensor.scale != 1.0:
                    scaled = round(parsed * sensor.scale, 2)
                    result[sensor.name] = scaled
                    log.debug("[%s] %s [%d] = %r -> %d -> %.2f (scale %.2f)",
                              cmd, sensor.name, sensor.index, raw_val, parsed, scaled, sensor.scale)
                else:
                    result[sensor.name] = parsed
                    log.debug("[%s] %s [%d] = %r -> %d",
                              cmd, sensor.name, sensor.index, raw_val, parsed)
            except (ValueError, TypeError):
                log.warning("[%s] field %d (%s) value %r not numeric",
                            cmd, sensor.index, sensor.name, raw_val)
                result[sensor.name] = raw_val

    # Post-process: translate operating mode
    if cmd == "MOD" and "operating_mode" in result:
        val = str(result["operating_mode"])
        translated = MODE_MAP.get(val, f"Unknown ({val})")
        log.debug("[MOD] operating_mode: %r -> %s", val, translated)
        result["operating_mode"] = translated

    # Post-process: translate battery type to human string for sensor display
    if cmd == "PIRI" and "battery_type" in result:
        bt = result["battery_type"]
        bt_name = BATTERY_TYPE_MAP.get(bt, f"Unknown ({bt})")
        log.debug("[PIRI] battery_type: %r -> %s", bt, bt_name)
        result["battery_type_name"] = bt_name

    return result
