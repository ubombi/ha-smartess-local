"""Select platform for SmartESS Local integration -- enum-style inverter settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity
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
class SelectDef:
    """Definition for a select entity."""

    key: str
    name: str
    p17_template: str
    options: dict[int, str]
    icon: str | None = None
    refresh_cmd: str = "PIRI"
    entity_category: EntityCategory | None = None


SELECT_DEFS: list[SelectDef] = [
    SelectDef(
        key="output_source_priority",
        name="Output Source Priority",
        p17_template="POP{value}",
        options={
            0: "Solar > Utility > Battery",
            1: "Solar > Battery > Utility",
        },
        icon="mdi:transmission-tower",
    ),
    SelectDef(
        key="charger_source_priority",
        name="Charger Source Priority",
        p17_template="PSP{value}",
        options={
            0: "Utility first",
            1: "Solar first",
            2: "Solar + Utility",
            3: "Solar only",
        },
        icon="mdi:ev-station",
    ),
    # --- Advanced (under Configuration section) ---
    SelectDef(
        key="battery_type",
        name="Battery Type",
        p17_template="PBT{value}",
        options={
            0: "AGM",
            1: "Flooded",
            2: "User-defined",
            3: "Pylontech",
            4: "Weco",
            5: "Soltaro",
            6: "BAK",
            7: "Lithium (LIB)",
            8: "Lithium Iron (LIC)",
        },
        icon="mdi:battery-outline",
        entity_category=EntityCategory.CONFIG,
    ),
    SelectDef(
        key="input_voltage_range",
        name="Input Voltage Range",
        p17_template="PGR{value}",
        options={0: "Appliance", 1: "UPS"},
        icon="mdi:sine-wave",
        entity_category=EntityCategory.CONFIG,
    ),
    SelectDef(
        key="output_mode",
        name="Output Mode",
        p17_template="POPM{value},0",
        options={
            0: "Single machine",
            1: "Parallel",
            2: "Phase 1 of 3",
            3: "Phase 2 of 3",
            4: "Phase 3 of 3",
        },
        icon="mdi:power-plug",
        entity_category=EntityCategory.CONFIG,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up inverter select entities."""
    coordinator: InverterCoordinator = entry.runtime_data

    entities: list[InverterSelect] = []
    for devaddr in coordinator.inverter_addresses:
        for sd in SELECT_DEFS:
            entities.append(InverterSelect(coordinator, sd, devaddr))

    async_add_entities(entities)
    logger.info("Added %d select entities", len(entities))


class InverterSelect(CoordinatorEntity[InverterCoordinator], SelectEntity):
    """An inverter select setting."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: InverterCoordinator, defn: SelectDef, devaddr: int) -> None:
        super().__init__(coordinator)
        self._defn = defn
        self._devaddr = devaddr
        self._int_to_label = defn.options
        self._label_to_int = {v: k for k, v in defn.options.items()}

        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{devaddr}_{defn.key}_select"
        self._attr_name = defn.name
        self._attr_options = list(defn.options.values())
        self._attr_icon = defn.icon
        self._attr_entity_category = defn.entity_category

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(**self.coordinator.device_info_dict(self._devaddr))

    @property
    def available(self) -> bool:
        return self.coordinator.connected

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.inverter_data.get(self._devaddr, {})
        raw = data.get(self._defn.key)
        if raw is None:
            return None
        try:
            label = self._int_to_label.get(int(raw))
            logger.debug("[addr=%d] %s current: raw=%r -> %s",
                         self._devaddr, self._defn.key, raw, label)
            return label
        except (ValueError, TypeError):
            logger.debug("[addr=%d] %s current: raw=%r (cannot convert)",
                         self._devaddr, self._defn.key, raw)
            return None

    async def async_select_option(self, option: str) -> None:
        """Set the inverter parameter."""
        int_val = self._label_to_int.get(option)
        if int_val is None:
            logger.error("[addr=%d] Unknown option %r for %s",
                         self._devaddr, option, self._defn.key)
            return

        cmd = self._defn.p17_template.format(value=int_val)
        logger.debug("[addr=%d] Setting %s -> %s (cmd=%s)",
                     self._devaddr, self._defn.key, option, cmd)

        ok = await self.coordinator.async_send_set_command(cmd, devaddr=self._devaddr)
        if ok:
            # Optimistic update
            self.coordinator.inverter_data.setdefault(self._devaddr, {})[self._defn.key] = int_val
            self.async_write_ha_state()
            if self._defn.refresh_cmd:
                await self.coordinator.async_refresh_command(self._defn.refresh_cmd, self._devaddr)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
