"""SmartESS Local integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.smartess_local.const import DOMAIN, PLATFORMS  # noqa: F401
from custom_components.smartess_local.coordinator import InverterCoordinator

logger = logging.getLogger(__name__)

type SmartessConfigEntry = ConfigEntry[InverterCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SmartessConfigEntry) -> bool:
    """Set up SmartESS Local from a config entry."""
    logger.debug("Setting up SmartESS Local entry: %s data=%s options=%s",
                 entry.entry_id, entry.data, entry.options)

    coordinator = InverterCoordinator(hass, entry)
    await coordinator.async_setup()

    entry.runtime_data = coordinator

    # Entity platforms are loaded later by the coordinator after RS485 bus
    # discovery completes (in _on_collector_connect, after first heartbeat).

    # Reload on options change (poll intervals)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    logger.info("SmartESS Local integration setup complete (entry=%s)", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartessConfigEntry) -> bool:
    """Unload a config entry."""
    logger.debug("Unloading SmartESS Local entry: %s", entry.entry_id)
    coordinator: InverterCoordinator = entry.runtime_data

    # Only unload platforms if they were loaded (discovery may not have completed)
    if coordinator._platforms_loaded:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if not unload_ok:
            return False

    await coordinator.async_shutdown()
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow user to remove a device (and its entities) from the UI."""
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry update (options changed). Reload to apply."""
    logger.info("Options changed for %s -- reloading", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
