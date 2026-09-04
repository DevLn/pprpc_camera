"""PPRPC switches.

Only the status indicator LED lives here. StatusLedSet(2633) is write-only --
no getter has ever been captured -- so the switch is assumed-state: it shows
what was last sent from Home Assistant and starts out unknown.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import PPRPCDevice
from .entity import PPRPCBaseEntity

STATUS_LED_DESCRIPTION = SwitchEntityDescription(
    key="status_led",
    translation_key="status_led",
    entity_category=EntityCategory.CONFIG,
    icon="mdi:led-on",
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the PPRPC switch platform."""
    device = hass.data[DOMAIN][config_entry.unique_id]

    async_add_entities([PPRPCStatusLedSwitch(device)])


class PPRPCStatusLedSwitch(PPRPCBaseEntity, SwitchEntity):
    """The camera's status indicator LED (write-only on the wire)."""

    # The camera cannot report the LED state, so this can never be anything
    # but assumed.
    _attr_assumed_state = True
    # Unknown until the first command is sent from here.
    _attr_is_on = None

    def __init__(self, device: PPRPCDevice) -> None:
        """Initialize the switch."""
        super().__init__(device)
        self.entity_description = STATUS_LED_DESCRIPTION
        self._attr_unique_id = f"{self.device.dev_id}_status_led"

    async def _async_set(self, is_on: bool) -> None:
        await self.device.async_set_status_led(is_on)
        # Commit state only after the command succeeds, so a failed command
        # doesn't leave the UI showing the wrong state.
        self._attr_is_on = is_on
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the status LED on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the status LED off."""
        await self._async_set(False)
