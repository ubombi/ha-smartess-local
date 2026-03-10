"""Switch platform for SmartESS Local integration -- toggle settings (buzzer, etc.).

Flag indices verified against 0x0994 firmware (PI17 protocol).
FLAG response is comma-separated 0/1 values: "0,1,1,1,0,1,1,1,0,1"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.smartess_local.const import DOMAIN
from custom_components.smartess_local.coordinator import InverterCoordinator

logger = logging.getLogger(__name__)


@dataclass
class SwitchDef:
    """Definition for a switch entity."""

    key: str
    name: str
    p17_on: str           # PE{letter} command
    p17_off: str          # PD{letter} command
    flag_key: str         # key in coordinator data from FLAG sensor, e.g. "flag_buzzer"
    icon_on: str = "mdi:toggle-switch"
    icon_off: str = "mdi:toggle-switch-off"
    refresh_cmd: str = "FLAG"
    entity_category: EntityCategory | None = None


# Flag index → sensor key mapping (from sensors.py _FLAG_SENSORS):
#   0: flag_buzzer                 → PEA/PDA
#   1: flag_overload_bypass        → PEB/PDB
#   2: flag_lcd_return_to_default  → PEC/PDC
#   3: flag_overload_restart       → PED/PDD
#   4: flag_temperature_restart    → PEE/PDE
#   5: flag_backlight              → PEF/PDF
#   6: flag_primary_source_alarm   → PEG/PDG
#   7: flag_fault_code_record      → PEH/PDH
#   8: flag_grid_frequency_power   → PEI/PDI
#   9: flag_battery_immediate_turnon → PEJ/PDJ

SWITCH_DEFS: list[SwitchDef] = [
    # --- Basic (prominent on dashboard) ---
    SwitchDef(
        key="buzzer",
        name="Buzzer",
        p17_on="PEA",
        p17_off="PDA",
        flag_key="flag_buzzer",
        icon_on="mdi:volume-high",
        icon_off="mdi:volume-off",
    ),
    # --- Advanced (under Configuration section) ---
    SwitchDef(
        key="overload_bypass",
        name="Overload Bypass",
        p17_on="PEB",
        p17_off="PDB",
        flag_key="flag_overload_bypass",
        icon_on="mdi:transfer-right",
        icon_off="mdi:transfer-right",
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchDef(
        key="lcd_return_to_default",
        name="LCD Return to Default",
        p17_on="PEC",
        p17_off="PDC",
        flag_key="flag_lcd_return_to_default",
        icon_on="mdi:monitor-dashboard",
        icon_off="mdi:monitor-off",
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchDef(
        key="overload_restart",
        name="Overload Restart",
        p17_on="PED",
        p17_off="PDD",
        flag_key="flag_overload_restart",
        icon_on="mdi:restart-alert",
        icon_off="mdi:restart-off",
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchDef(
        key="temperature_restart",
        name="Temperature Restart",
        p17_on="PEE",
        p17_off="PDE",
        flag_key="flag_temperature_restart",
        icon_on="mdi:thermometer-alert",
        icon_off="mdi:thermometer-off",
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchDef(
        key="backlight",
        name="Backlight",
        p17_on="PEF",
        p17_off="PDF",
        flag_key="flag_backlight",
        icon_on="mdi:brightness-7",
        icon_off="mdi:brightness-4",
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchDef(
        key="primary_source_alarm",
        name="Primary Source Alarm",
        p17_on="PEG",
        p17_off="PDG",
        flag_key="flag_primary_source_alarm",
        icon_on="mdi:alarm-light",
        icon_off="mdi:alarm-light-off",
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchDef(
        key="fault_code_record",
        name="Fault Code Record",
        p17_on="PEH",
        p17_off="PDH",
        flag_key="flag_fault_code_record",
        icon_on="mdi:clipboard-text",
        icon_off="mdi:clipboard-text-off",
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchDef(
        key="grid_frequency_power",
        name="Grid Frequency Power",
        p17_on="PEI",
        p17_off="PDI",
        flag_key="flag_grid_frequency_power",
        icon_on="mdi:sine-wave",
        icon_off="mdi:sine-wave",
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchDef(
        key="battery_immediate_turnon",
        name="Battery Immediate Turn-on",
        p17_on="PEJ",
        p17_off="PDJ",
        flag_key="flag_battery_immediate_turnon",
        icon_on="mdi:battery-charging",
        icon_off="mdi:battery-off",
        entity_category=EntityCategory.CONFIG,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up inverter switch entities."""
    coordinator: InverterCoordinator = entry.runtime_data

    entities: list[InverterSwitch] = []
    for devaddr in coordinator.inverter_addresses:
        for sd in SWITCH_DEFS:
            entities.append(InverterSwitch(coordinator, sd, devaddr))

    async_add_entities(entities)
    logger.info("Added %d switch entities", len(entities))


class InverterSwitch(CoordinatorEntity[InverterCoordinator], SwitchEntity):
    """An inverter toggle switch."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator: InverterCoordinator, defn: SwitchDef, devaddr: int) -> None:
        super().__init__(coordinator)
        self._defn = defn
        self._devaddr = devaddr
        self._optimistic_state: bool | None = None

        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{devaddr}_{defn.key}_switch"
        self._attr_name = defn.name
        self._attr_entity_category = defn.entity_category

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(**self.coordinator.device_info_dict(self._devaddr))

    @property
    def available(self) -> bool:
        return self.coordinator.connected

    def _read_flag(self) -> bool | None:
        """Read this switch's state from coordinator FLAG data."""
        data = self.coordinator.inverter_data.get(self._devaddr, {})
        val = data.get(self._defn.flag_key)
        if val is None:
            return None
        # FLAG sensor stores int 0/1
        return bool(val)

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_state is not None:
            return self._optimistic_state
        return self._read_flag()

    @property
    def icon(self) -> str:
        if self.is_on:
            return self._defn.icon_on
        return self._defn.icon_off

    async def async_turn_on(self, **kwargs) -> None:
        logger.debug("[addr=%d] Turning ON %s (cmd=%s)",
                     self._devaddr, self._defn.key, self._defn.p17_on)
        ok = await self.coordinator.async_send_set_command(self._defn.p17_on, devaddr=self._devaddr)
        if ok:
            self._optimistic_state = True
            self.async_write_ha_state()
            await self.coordinator.async_refresh_command(self._defn.refresh_cmd, self._devaddr)

    async def async_turn_off(self, **kwargs) -> None:
        logger.debug("[addr=%d] Turning OFF %s (cmd=%s)",
                     self._devaddr, self._defn.key, self._defn.p17_off)
        ok = await self.coordinator.async_send_set_command(self._defn.p17_off, devaddr=self._devaddr)
        if ok:
            self._optimistic_state = False
            self.async_write_ha_state()
            await self.coordinator.async_refresh_command(self._defn.refresh_cmd, self._devaddr)

    @callback
    def _handle_coordinator_update(self) -> None:
        # Only clear optimistic state if FLAG data can resolve this switch.
        # Otherwise keep the optimistic value to avoid "unknown" flicker.
        real = self._read_flag()
        if real is not None:
            self._optimistic_state = None
        self.async_write_ha_state()
