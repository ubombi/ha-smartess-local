"""Sensor platform for SmartESS Local integration.

Creates one sensor entity per (inverter_address, sensor_def) combination,
plus computed power and integrated energy sensors for the Energy Dashboard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from custom_components.smartess_local.const import DOMAIN
from custom_components.smartess_local.coordinator import InverterCoordinator
from custom_components.smartess_local.inverter.sensors import SENSOR_MAP, SensorDef
from custom_components.smartess_local.inverter.energy import EnergyAccumulator

logger = logging.getLogger(__name__)

_DEVICE_CLASS_MAP: dict[str, SensorDeviceClass] = {
    "voltage": SensorDeviceClass.VOLTAGE,
    "current": SensorDeviceClass.CURRENT,
    "power": SensorDeviceClass.POWER,
    "apparent_power": SensorDeviceClass.APPARENT_POWER,
    "energy": SensorDeviceClass.ENERGY,
    "frequency": SensorDeviceClass.FREQUENCY,
    "temperature": SensorDeviceClass.TEMPERATURE,
    "battery": SensorDeviceClass.BATTERY,
}

_STATE_CLASS_MAP: dict[str, SensorStateClass] = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
    "total": SensorStateClass.TOTAL,
}

# ---------------------------------------------------------------------------
# Computed power + integrated energy sensors for Energy Dashboard
# ---------------------------------------------------------------------------


@dataclass
class EnergySensorDef:
    """Definition for a computed/integrated energy sensor."""
    key: str
    name: str
    unit: str
    device_class: str
    state_class: str
    icon: str
    source_keys: list[str]  # coordinator data keys needed for computation
    compute: str            # "multiply" for V*I, "passthrough" for direct
    accumulate: bool        # True = integrate W->kWh, False = instantaneous


ENERGY_SENSOR_DEFS: list[EnergySensorDef] = [
    # Instantaneous computed power (W)
    EnergySensorDef(
        key="battery_charging_power",
        name="Battery Charging Power",
        unit="W",
        device_class="power",
        state_class="measurement",
        icon="mdi:battery-charging",
        source_keys=["battery_voltage", "battery_charging_current"],
        compute="multiply",
        accumulate=False,
    ),
    EnergySensorDef(
        key="battery_discharging_power",
        name="Battery Discharging Power",
        unit="W",
        device_class="power",
        state_class="measurement",
        icon="mdi:battery-arrow-down",
        source_keys=["battery_voltage", "battery_discharge_current"],
        compute="multiply",
        accumulate=False,
    ),
    # Integrated energy counters (kWh)
    EnergySensorDef(
        key="ac_output_energy",
        name="AC Output Energy",
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        icon="mdi:flash",
        source_keys=["ac_output_active_power"],
        compute="passthrough",
        accumulate=True,
    ),
    EnergySensorDef(
        key="battery_charge_energy",
        name="Battery Charge Energy",
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        icon="mdi:battery-charging",
        source_keys=["battery_voltage", "battery_charging_current"],
        compute="multiply",
        accumulate=True,
    ),
    EnergySensorDef(
        key="battery_discharge_energy",
        name="Battery Discharge Energy",
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        icon="mdi:battery-arrow-down",
        source_keys=["battery_voltage", "battery_discharge_current"],
        compute="multiply",
        accumulate=True,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SmartESS Local sensors from config entry."""
    coordinator: InverterCoordinator = entry.runtime_data

    entities: list[InverterSensor] = []
    for devaddr in coordinator.inverter_addresses:
        for cmd, sensors in SENSOR_MAP.items():
            for sensor_def in sensors:
                entities.append(InverterSensor(coordinator, sensor_def, cmd, devaddr))

    energy_entities: list[InverterEnergySensor] = []
    for devaddr in coordinator.inverter_addresses:
        for edef in ENERGY_SENSOR_DEFS:
            energy_entities.append(InverterEnergySensor(coordinator, edef, devaddr))

    async_add_entities(entities + energy_entities)
    logger.info("Added %d sensor + %d energy entities across %d inverter(s)",
                len(entities), len(energy_entities), len(coordinator.inverter_addresses))


class InverterSensor(CoordinatorEntity[InverterCoordinator], SensorEntity):
    """A single inverter sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: InverterCoordinator,
        sensor_def: SensorDef,
        cmd: str,
        devaddr: int,
    ) -> None:
        super().__init__(coordinator)
        self._sensor_def = sensor_def
        self._cmd = cmd
        self._devaddr = devaddr

        # Unique ID includes entry + devaddr for multi-inverter / multi-collector
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{devaddr}_{sensor_def.name}"
        self._attr_name = sensor_def.label if sensor_def.label else sensor_def.name.replace("_", " ").title()
        self._attr_native_unit_of_measurement = sensor_def.unit
        self._attr_icon = sensor_def.icon

        if sensor_def.device_class:
            dc = _DEVICE_CLASS_MAP.get(sensor_def.device_class)
            if dc:
                self._attr_device_class = dc

        if sensor_def.state_class:
            sc = _STATE_CLASS_MAP.get(sensor_def.state_class)
            if sc:
                self._attr_state_class = sc

        # PIRI ratings, technical fields etc. hidden by default
        self._attr_entity_registry_enabled_default = sensor_def.enabled_default

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(**self.coordinator.device_info_dict(self._devaddr))

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        data = self.coordinator.inverter_data.get(self._devaddr, {})
        return self._sensor_def.name in data

    @property
    def native_value(self) -> Any:
        data = self.coordinator.inverter_data.get(self._devaddr, {})
        return data.get(self._sensor_def.name)


class InverterEnergySensor(RestoreEntity, CoordinatorEntity[InverterCoordinator], SensorEntity):
    """Computed power or integrated energy sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: InverterCoordinator,
        defn: EnergySensorDef,
        devaddr: int,
    ) -> None:
        super().__init__(coordinator)
        self._defn = defn
        self._devaddr = devaddr
        self._accumulator: EnergyAccumulator | None = None

        if defn.accumulate:
            self._accumulator = EnergyAccumulator()

        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{devaddr}_{defn.key}"
        self._attr_name = defn.name
        self._attr_native_unit_of_measurement = defn.unit
        self._attr_icon = defn.icon

        dc = _DEVICE_CLASS_MAP.get(defn.device_class)
        if dc:
            self._attr_device_class = dc

        sc = _STATE_CLASS_MAP.get(defn.state_class)
        if sc:
            self._attr_state_class = sc

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(**self.coordinator.device_info_dict(self._devaddr))

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        if self._accumulator and (last := await self.async_get_last_state()):
            try:
                restored = float(last.state)
                self._accumulator.total_kwh = restored
                logger.debug("Restored %s = %.4f kWh", self._defn.key, restored)
            except (ValueError, TypeError):
                pass

    def _compute_power(self) -> float | None:
        """Compute instantaneous power from source keys."""
        data = self.coordinator.inverter_data.get(self._devaddr, {})

        if self._defn.compute == "passthrough":
            val = data.get(self._defn.source_keys[0])
            return float(val) if val is not None else None

        if self._defn.compute == "multiply":
            values = []
            for key in self._defn.source_keys:
                val = data.get(key)
                if val is None:
                    return None
                values.append(float(val))
            result = 1.0
            for v in values:
                result *= v
            return result

        return None

    @property
    def available(self) -> bool:
        return self._compute_power() is not None

    @property
    def native_value(self) -> float | None:
        power = self._compute_power()
        if power is None:
            return self._accumulator.total_kwh if self._accumulator else None

        if self._accumulator:
            return self._accumulator.accumulate(power, datetime.now())

        return round(power, 1)

    @callback
    def _handle_coordinator_update(self) -> None:
        # For accumulators, trigger accumulation on every update
        if self._accumulator:
            power = self._compute_power()
            if power is not None:
                self._accumulator.accumulate(power, datetime.now())
        self.async_write_ha_state()
