"""Config flow for Balboa Robust: setup (host/port) + options (all tunables)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_AUTO_PAUSE_AFTER,
    CONF_BACKOFF_FACTOR,
    CONF_BACKOFF_INITIAL,
    CONF_BACKOFF_MAX,
    CONF_CONNECT_TIMEOUT,
    CONF_HEARTBEAT_INTERVAL,
    CONF_MAX_RETRIES,
    CONF_RECONNECT_ON_ERROR,
    CONF_STABLE_FOR,
    CONF_STALE_AFTER,
    CONF_UPTIME_WINDOW,
    DEFAULT_PORT,
    DEFAULTS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Spa"

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


def _num(
    minimum: float, maximum: float, step: float = 1, unit: str | None = None
) -> NumberSelector:
    kwargs: dict[str, Any] = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": NumberSelectorMode.BOX,
    }
    if unit is not None:
        kwargs["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**kwargs))


def options_schema(current: dict[str, Any]) -> vol.Schema:
    """Options UI: every connection tunable, prefilled with current values."""

    def d(key: str) -> Any:
        return current.get(key, DEFAULTS[key])

    return vol.Schema(
        {
            vol.Required(
                CONF_CONNECT_TIMEOUT, default=d(CONF_CONNECT_TIMEOUT)
            ): _num(1, 30, 0.5, "s"),
            vol.Required(
                CONF_BACKOFF_INITIAL, default=d(CONF_BACKOFF_INITIAL)
            ): _num(1, 60, 1, "s"),
            vol.Required(CONF_BACKOFF_MAX, default=d(CONF_BACKOFF_MAX)): _num(
                10, 3600, 5, "s"
            ),
            vol.Required(
                CONF_BACKOFF_FACTOR, default=d(CONF_BACKOFF_FACTOR)
            ): _num(1.1, 5, 0.1),
            vol.Required(
                CONF_HEARTBEAT_INTERVAL, default=d(CONF_HEARTBEAT_INTERVAL)
            ): _num(2, 120, 1, "s"),
            vol.Required(CONF_STALE_AFTER, default=d(CONF_STALE_AFTER)): _num(
                5, 600, 5, "s"
            ),
            vol.Required(CONF_MAX_RETRIES, default=d(CONF_MAX_RETRIES)): _num(
                0, 1000, 1
            ),
            vol.Required(
                CONF_AUTO_PAUSE_AFTER, default=d(CONF_AUTO_PAUSE_AFTER)
            ): _num(0, 1000, 1),
            vol.Required(
                CONF_STABLE_FOR, default=d(CONF_STABLE_FOR)
            ): _num(0, 120, 1, "s"),
            vol.Required(
                CONF_UPTIME_WINDOW, default=d(CONF_UPTIME_WINDOW)
            ): _num(300, 86400, 300, "s"),
            vol.Required(
                CONF_RECONNECT_ON_ERROR, default=d(CONF_RECONNECT_ON_ERROR)
            ): BooleanSelector(),
        }
    )


async def _probe_once(host: str, port: int) -> str | None:
    """One connect+configuration-loaded probe. Returns error key or None."""
    from pybalboa import SpaClient

    client = SpaClient(host, port)
    try:
        connected = await asyncio.wait_for(client.connect(), timeout=10)
        if not connected:
            return "cannot_connect"
        loaded = await asyncio.wait_for(
            client.async_configuration_loaded(), timeout=10
        )
        if not loaded:
            return "no_spa_data"
        return None
    except asyncio.TimeoutError:
        return "timeout"
    except OSError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected validation error")
        return "unknown"
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5)
        except Exception:  # noqa: BLE001
            pass


async def _validate_connection(host: str, port: int) -> str | None:
    """Single fast probe (~15s max).

    Setup no longer waits out the module's stale windows synchronously — that
    was up to 3 minutes of spinner for the 50350's dead-zone case. Instead we
    do one quick probe: if it succeeds, great, entities discover immediately;
    if it times out or the host is unreachable, we still create the entry and
    the connection manager takes over in the background. Only genuine config
    errors (garbage on the port, unknown exceptions) block entry creation.
    """
    result = await _probe_once(host, port)
    if result is None:
        _LOGGER.info("balboa_robust setup probe succeeded")
    else:
        _LOGGER.info("balboa_robust setup probe result: %s", result)
    return result


class BalboaRobustConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup: host + port, with live connection validation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            name = (user_input.get(CONF_NAME) or DEFAULT_NAME).strip() or DEFAULT_NAME
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            error = await _validate_connection(host, port)
            # Timeout/cannot_connect is expected for a known-flaky module in a
            # long dead window — still create the entry so the connection
            # manager can take over. Real config problems (unknown, no_spa_data
            # indicating the port answers with garbage) still block.
            if error in (None, "timeout", "cannot_connect"):
                if error is not None:
                    _LOGGER.warning(
                        "balboa_robust: creating entry despite probe %s — "
                        "manager will keep retrying in the background",
                        error,
                    )
                return self.async_create_entry(
                    title=name,
                    data={CONF_HOST: host, CONF_PORT: port},
                    options=dict(DEFAULTS),
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BalboaRobustOptionsFlow()


class BalboaRobustOptionsFlow(OptionsFlow):
    """Options: connection/backoff/heartbeat tunables, hot-applied.

    Also exposes a rename step so existing installs (which were created
    before we asked for a display name) can adopt a friendlier device
    name without touching unique_ids.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "rename"]
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="settings",
            data_schema=options_schema(dict(self.config_entry.options)),
        )

    async def async_step_rename(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            new_title = (user_input.get(CONF_NAME) or "").strip()
            if new_title:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, title=new_title
                )
            return self.async_create_entry(data=dict(self.config_entry.options))

        return self.async_show_form(
            step_id="rename",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=self.config_entry.title
                    ): str,
                }
            ),
        )
