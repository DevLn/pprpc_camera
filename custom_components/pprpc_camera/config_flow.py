"""Config flow to configure the PPRPC Camera integration.

There are no credentials anywhere in this flow: LanAuth uses a single-use
token (lsign) minted by UDP discovery, so knowing the camera's IP is the
whole configuration. Connection parameters live in entry.options so the
options flow can adjust them without touching entry.data.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from aiopprpc import find_device
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_DEVICE_ID, CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.typing import DiscoveryInfoType

from .config_helpers import (
    get_idle_disconnect_delay,
    get_info_poll_interval,
    get_status_poll_interval,
)
from .const import (
    CONF_IDLE_DISCONNECT_DELAY,
    CONF_INFO_POLL_INTERVAL,
    CONF_STATUS_POLL_INTERVAL,
    DOMAIN,
    LOGGER,
    SOURCE_DISCOVERY_CONFIRM,
)


@callback
def async_get_schema(
    defaults: dict[str, Any], host_readonly: bool = False
) -> vol.Schema:
    """Return the PPRPC Camera schema (host only -- there are no credentials)."""
    host_type = selector.TextSelector(
        selector.TextSelectorConfig(
            type=selector.TextSelectorType.TEXT, read_only=host_readonly
        )
    )
    return vol.Schema({vol.Required(CONF_HOST, default=defaults.get(CONF_HOST)): host_type})


async def async_validate_input(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[dict[str, str], str]:
    """Probe the camera by discovery and return (errors, dev_id)."""
    errors = {}
    field = "base"
    dev_descriptor = None

    try:
        dev_descriptor = await find_device(user_input[CONF_HOST])
    except TimeoutError:
        LOGGER.exception("Cannot connect to %s", user_input[CONF_HOST])
        errors[field] = "cannot_connect"
    except Exception:
        # Any other failure should surface as a friendly cannot_connect error
        # instead of aborting the flow.
        LOGGER.exception("Unexpected error connecting to %s", user_input[CONF_HOST])
        errors[field] = "cannot_connect"

    return errors, dev_descriptor.dev_id if dev_descriptor else ""


class PPRPCCameraFlowHandler(ConfigFlow, domain=DOMAIN):
    """Config flow for PPRPC Camera."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlowWithReload:
        """Get the options flow for this handler."""
        return PPRPCCameraOptionsFlowHandler()

    async def async_step_integration_discovery(
        self, discovery_info: DiscoveryInfoType
    ) -> ConfigFlowResult:
        """Handle discovery of a PPRPC camera."""
        host = discovery_info[CONF_HOST]
        device_id = discovery_info[CONF_DEVICE_ID]
        name = f"{device_id} ({host})"

        # Check if already configured
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        # Check if host is already configured with a different device ID
        self._async_abort_entries_match({CONF_HOST: host})

        self.context["title_placeholders"] = {CONF_NAME: name}
        self.context["discovery_info"] = discovery_info

        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery of a PPRPC camera."""
        errors: dict[str, str] = {}

        discovery_info = self.context["discovery_info"]

        if user_input is not None:
            # Always use the discovered host: the form's host field is read-only.
            data = {CONF_HOST: discovery_info[CONF_HOST]}
            errors, dev_id = await async_validate_input(self.hass, data)

            if not errors:
                # Verify the device ID matches what discovery reported.
                if dev_id == discovery_info[CONF_DEVICE_ID]:
                    return self.async_create_entry(
                        title=discovery_info[CONF_DEVICE_ID],
                        data={},
                        options={CONF_HOST: discovery_info[CONF_HOST]},
                    )
                errors["base"] = "device_mismatch"

        return self.async_show_form(
            step_id=SOURCE_DISCOVERY_CONFIRM,
            data_schema=async_get_schema(
                {CONF_HOST: discovery_info[CONF_HOST]}, host_readonly=True
            ),
            errors=errors,
            description_placeholders={
                CONF_NAME: discovery_info[CONF_DEVICE_ID],
                CONF_HOST: discovery_info[CONF_HOST],
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, dev_id = await async_validate_input(self.hass, user_input)
            if not errors:
                self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})

                await self.async_set_unique_id(dev_id, raise_on_progress=False)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=dev_id,
                    data={},
                    options={CONF_HOST: user_input[CONF_HOST]},
                )
        else:
            user_input = {}

        return self.async_show_form(
            step_id=SOURCE_USER,
            data_schema=async_get_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration (e.g. the camera's IP changed)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, dev_id = await async_validate_input(self.hass, user_input)
            if not errors:
                await self.async_set_unique_id(dev_id, raise_on_progress=False)
                self._abort_if_unique_id_mismatch()

                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    options={
                        # Keep tuned intervals from the options flow.
                        **self._get_reconfigure_entry().options,
                        CONF_HOST: user_input[CONF_HOST],
                    },
                )
        else:
            user_input = {
                CONF_HOST: self._get_reconfigure_entry().options[CONF_HOST],
            }

        return self.async_show_form(
            step_id=SOURCE_RECONFIGURE,
            data_schema=async_get_schema(user_input),
            errors=errors,
            description_placeholders={
                CONF_NAME: self._get_reconfigure_entry().unique_id,
                CONF_HOST: self._get_reconfigure_entry().options[CONF_HOST],
            },
        )


class PPRPCCameraOptionsFlowHandler(OptionsFlowWithReload):
    """Handle PPRPC Camera options (per-entry overrides)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage PPRPC Camera options."""
        if user_input is not None:
            # Merge over the existing options so the host is preserved.
            options = {**self.config_entry.options, **user_input}
            return self.async_create_entry(title="", data=options)

        current_delay = self.config_entry.options.get(
            CONF_IDLE_DISCONNECT_DELAY, get_idle_disconnect_delay(self.hass)
        )
        current_poll = self.config_entry.options.get(
            CONF_STATUS_POLL_INTERVAL, get_status_poll_interval(self.hass)
        )
        current_info_poll = self.config_entry.options.get(
            CONF_INFO_POLL_INTERVAL, get_info_poll_interval(self.hass)
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_IDLE_DISCONNECT_DELAY, default=current_delay
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=3600, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_STATUS_POLL_INTERVAL, default=current_poll
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=86400, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_INFO_POLL_INTERVAL, default=current_info_poll
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=86400, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
