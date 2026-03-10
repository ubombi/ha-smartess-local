"""Config flow for SmartESS Local integration.

Supports multiple config entries (one per collector).
Options flow for per-entry poll intervals and inverter count.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from custom_components.smartess_local.const import (
    DOMAIN,
    CONF_SERVER_IP,
    CONF_TCP_PORT,
    CONF_UDP_PORT,
    CONF_UDP_BROADCAST_IP,
    CONF_HEARTBEAT_INTERVAL,
    CONF_INVERTER_COUNT,
    CONF_POLL_FAST,
    CONF_POLL_MEDIUM,
    CONF_POLL_SLOW,
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    DEFAULT_UDP_BROADCAST_IP,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_INVERTER_COUNT,
    DEFAULT_POLL_FAST,
    DEFAULT_POLL_MEDIUM,
    DEFAULT_POLL_SLOW,
)

logger = logging.getLogger(__name__)


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def _compute_broadcast_24(ip: str) -> str:
    """Compute /24 subnet broadcast from an IPv4 address."""
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    return "255.255.255.255"


def _validate_ip(ip: str) -> bool:
    try:
        socket.inet_aton(ip)
        return True
    except OSError:
        return False


class SmartessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartESS Local."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow handler."""
        return SmartessOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            server_ip = user_input.get(CONF_SERVER_IP, "")
            tcp_port = user_input.get(CONF_TCP_PORT, DEFAULT_TCP_PORT)

            if not _validate_ip(server_ip):
                errors[CONF_SERVER_IP] = "invalid_ip"

            if not errors:
                # Unique ID = server:port so you can't add the same server twice
                unique_id = f"{server_ip}:{tcp_port}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                logger.debug("Creating config entry: %s", user_input)
                return self.async_create_entry(
                    title=f"SmartESS Local ({server_ip}:{tcp_port})",
                    data=user_input,
                )

        local_ip = await self.hass.async_add_executor_job(_get_local_ip)
        default_broadcast = _compute_broadcast_24(local_ip) if local_ip else DEFAULT_UDP_BROADCAST_IP

        data_schema = vol.Schema(
            {
                vol.Required(CONF_SERVER_IP, default=local_ip): str,
                vol.Required(CONF_TCP_PORT, default=DEFAULT_TCP_PORT): int,
                vol.Required(CONF_UDP_PORT, default=DEFAULT_UDP_PORT): int,
                vol.Required(CONF_UDP_BROADCAST_IP, default=default_broadcast): str,
                vol.Required(CONF_HEARTBEAT_INTERVAL, default=DEFAULT_HEARTBEAT_INTERVAL): int,
                vol.Required(CONF_INVERTER_COUNT, default=DEFAULT_INVERTER_COUNT): vol.All(
                    int, vol.Range(min=1, max=16)
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )


class SmartessOptionsFlow(OptionsFlow):
    """Handle options for a SmartESS Local config entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage polling intervals and inverter count."""
        if user_input is not None:
            logger.debug("Options updated: %s", user_input)
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options
        # Also fall back to initial data for inverter_count
        inv_count = current.get(
            CONF_INVERTER_COUNT,
            self.config_entry.data.get(CONF_INVERTER_COUNT, DEFAULT_INVERTER_COUNT),
        )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_POLL_FAST,
                    default=current.get(CONF_POLL_FAST, DEFAULT_POLL_FAST),
                ): vol.All(int, vol.Range(min=0, max=3600)),
                vol.Required(
                    CONF_POLL_MEDIUM,
                    default=current.get(CONF_POLL_MEDIUM, DEFAULT_POLL_MEDIUM),
                ): vol.All(int, vol.Range(min=0, max=3600)),
                vol.Required(
                    CONF_POLL_SLOW,
                    default=current.get(CONF_POLL_SLOW, DEFAULT_POLL_SLOW),
                ): vol.All(int, vol.Range(min=0, max=86400)),
                vol.Required(
                    CONF_INVERTER_COUNT,
                    default=inv_count,
                ): vol.All(int, vol.Range(min=1, max=16)),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
        )
