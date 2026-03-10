"""Number platform for SmartESS Local integration -- numeric inverter settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.components.number import NumberEntity, NumberMode
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
class NumberDef:
    """Definition for a number entity.

    For simple commands: p17_template is formatted with {value}.
    For paired commands (MCHGV, BUCD): paired_cmd + paired_key + paired_position
    are used to build a combined command with the partner's current value.

    send_scale: multiply UI value before sending (e.g. 10.0 for tenths-of-volt).
    """

    key: str
    name: str
    p17_template: str
    min_value: float
    max_value: float
    step: float = 1.0
    unit: str | None = None
    icon: str | None = None
    mode: NumberMode = NumberMode.BOX
    refresh_cmd: str = "PIRI"
    entity_category: EntityCategory | None = None
    send_scale: float = 1.0
    paired_cmd: str = ""       # e.g. "MCHGV", "BUCD"
    paired_key: str = ""       # coordinator data key of the partner
    paired_position: int = 0   # 0 = first value in pair, 1 = second


NUMBER_DEFS: list[NumberDef] = [
    NumberDef(
        key="max_charging_current",
        name="Max Charging Current",
        p17_template="MCHGC0,{value:03d}",
        min_value=0,
        max_value=120,
        step=10,
        unit="A",
        icon="mdi:current-dc",
        entity_category=EntityCategory.CONFIG,
    ),
    NumberDef(
        key="max_ac_charging_current",
        name="Max AC Charging Current",
        p17_template="MUCHGC0,{value:03d}",
        min_value=0,
        max_value=120,
        step=10,
        unit="A",
        icon="mdi:current-ac",
        entity_category=EntityCategory.CONFIG,
    ),
    NumberDef(
        key="battery_under_voltage",
        name="Battery Cut-off Voltage",
        p17_template="PSDV{value:03d}",
        min_value=40.0,
        max_value=48.0,
        step=0.1,
        unit="V",
        icon="mdi:battery-alert",
        entity_category=EntityCategory.CONFIG,
        send_scale=10.0,
    ),
    # --- Paired: MCHGV sets bulk + float together ---
    NumberDef(
        key="battery_bulk_voltage",
        name="Battery Bulk Charge Voltage",
        p17_template="",  # unused — paired_cmd used instead
        min_value=48.0,
        max_value=58.4,
        step=0.1,
        unit="V",
        icon="mdi:battery-charging-high",
        entity_category=EntityCategory.CONFIG,
        send_scale=10.0,
        paired_cmd="MCHGV",
        paired_key="battery_float_voltage",
        paired_position=0,
    ),
    NumberDef(
        key="battery_float_voltage",
        name="Battery Float Charge Voltage",
        p17_template="",  # unused — paired_cmd used instead
        min_value=48.0,
        max_value=58.4,
        step=0.1,
        unit="V",
        icon="mdi:battery-charging-medium",
        entity_category=EntityCategory.CONFIG,
        send_scale=10.0,
        paired_cmd="MCHGV",
        paired_key="battery_bulk_voltage",
        paired_position=1,
    ),
    # --- Paired: BUCD sets recharge + redischarge together ---
    NumberDef(
        key="battery_recharge_voltage",
        name="Battery Re-charge Voltage",
        p17_template="",  # unused — paired_cmd used instead
        min_value=44.0,
        max_value=51.0,
        step=0.1,
        unit="V",
        icon="mdi:battery-sync",
        entity_category=EntityCategory.CONFIG,
        send_scale=10.0,
        paired_cmd="BUCD",
        paired_key="battery_redischarge_voltage",
        paired_position=0,
    ),
    NumberDef(
        key="battery_redischarge_voltage",
        name="Battery Re-discharge Voltage",
        p17_template="",  # unused — paired_cmd used instead
        min_value=0.0,
        max_value=58.0,
        step=0.1,
        unit="V",
        icon="mdi:battery-minus",
        entity_category=EntityCategory.CONFIG,
        send_scale=10.0,
        paired_cmd="BUCD",
        paired_key="battery_recharge_voltage",
        paired_position=1,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up inverter number entities."""
    coordinator: InverterCoordinator = entry.runtime_data

    entities: list[InverterNumber] = []
    for devaddr in coordinator.inverter_addresses:
        for nd in NUMBER_DEFS:
            entities.append(InverterNumber(coordinator, nd, devaddr))

    async_add_entities(entities)
    logger.info("Added %d number entities", len(entities))


class InverterNumber(CoordinatorEntity[InverterCoordinator], NumberEntity):
    """An inverter numeric setting."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: InverterCoordinator, defn: NumberDef, devaddr: int) -> None:
        super().__init__(coordinator)
        self._defn = defn
        self._devaddr = devaddr

        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{devaddr}_{defn.key}_number"
        self._attr_name = defn.name
        self._attr_native_min_value = defn.min_value
        self._attr_native_max_value = defn.max_value
        self._attr_native_step = defn.step
        self._attr_native_unit_of_measurement = defn.unit
        self._attr_icon = defn.icon
        self._attr_mode = defn.mode
        self._attr_entity_category = defn.entity_category

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(**self.coordinator.device_info_dict(self._devaddr))

    @property
    def available(self) -> bool:
        return self.coordinator.connected

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.inverter_data.get(self._devaddr, {})
        raw = data.get(self._defn.key)
        if raw is None:
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the inverter parameter."""
        defn = self._defn

        if defn.paired_cmd:
            cmd = self._build_paired_cmd(value)
            if cmd is None:
                return
        else:
            scaled = int(round(value * defn.send_scale))
            cmd = defn.p17_template.format(value=scaled)

        logger.info("[addr=%d] Setting %s = %s (cmd=%s)",
                    self._devaddr, defn.key, value, cmd)

        ok = await self.coordinator.async_send_set_command(cmd, devaddr=self._devaddr)
        if ok:
            self.coordinator.inverter_data.setdefault(self._devaddr, {})[defn.key] = value
            self.async_write_ha_state()
            if defn.refresh_cmd:
                await self.coordinator.async_refresh_command(defn.refresh_cmd, self._devaddr)

    def _build_paired_cmd(self, value: float) -> str | None:
        """Build a paired command (MCHGV/BUCD) combining this value with its partner."""
        defn = self._defn
        data = self.coordinator.inverter_data.get(self._devaddr, {})
        partner_raw = data.get(defn.paired_key)

        if partner_raw is None:
            logger.error(
                "[addr=%d] Cannot set %s: partner %s value unknown "
                "(PIRI data not yet received?)",
                self._devaddr, defn.key, defn.paired_key,
            )
            return None

        try:
            partner_val = float(partner_raw)
        except (ValueError, TypeError):
            logger.error(
                "[addr=%d] Cannot set %s: partner %s has invalid value %r",
                self._devaddr, defn.key, defn.paired_key, partner_raw,
            )
            return None

        my_tenths = int(round(value * defn.send_scale))
        partner_tenths = int(round(partner_val * defn.send_scale))

        if defn.paired_position == 0:
            first, second = my_tenths, partner_tenths
        else:
            first, second = partner_tenths, my_tenths

        return f"{defn.paired_cmd}{first:03d},{second:03d}"

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
