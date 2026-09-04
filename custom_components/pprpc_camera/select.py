"""Configuration selects for PPRPC cameras: flip, IR-cut, night light, motion.

Each select maps a wire enum to option strings via the enum's own member
names, so the option order and values always match aiopprpc's byte-verified
enums. State is cached in the device's last-known properties: the camera
reads these back at every connect, and a just-sent option is remembered
locally so the UI doesn't flicker to a stale reading in between.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from aiopprpc import FlipMode, IRCutMode, MotionSensitivity, NightLightMode
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import PPRPCDevice
from .entity import PPRPCBaseEntity


@dataclass(frozen=True, kw_only=True)
class PPRPCSelectEntityDescription(SelectEntityDescription):
    """Describes a PPRPC select entity."""

    enum: type[IntEnum]
    # Key in PPRPCDevice.properties holding the current raw value.
    property_key: str
    select_fn: Callable[
        [PPRPCDevice], Callable[[IntEnum], Coroutine[Any, Any, None]]
    ]


def _options(enum: type[IntEnum]) -> list[str]:
    return [member.name.lower() for member in enum]


SELECTS: tuple[PPRPCSelectEntityDescription, ...] = (
    PPRPCSelectEntityDescription(
        key="flip",
        translation_key="flip",
        entity_category=EntityCategory.CONFIG,
        options=_options(FlipMode),
        enum=FlipMode,
        property_key="flip",
        select_fn=lambda device: device.async_set_flip,
        icon="mdi:flip-vertical",
    ),
    PPRPCSelectEntityDescription(
        key="ircut",
        translation_key="ircut",
        entity_category=EntityCategory.CONFIG,
        options=_options(IRCutMode),
        enum=IRCutMode,
        property_key="ircut",
        select_fn=lambda device: device.async_set_ircut,
        icon="mdi:weather-night",
    ),
    PPRPCSelectEntityDescription(
        key="night_light",
        translation_key="night_light",
        entity_category=EntityCategory.CONFIG,
        options=_options(NightLightMode),
        enum=NightLightMode,
        property_key="night_light",
        select_fn=lambda device: device.async_set_night_light,
        # The mode LABELS rest on a note about which app buttons were pressed
        # during the capture, not on decoded bytes (the same evidence class
        # that once swapped motion low/medium) -- the values round-trip, the
        # names may not sit on the right values. See aiopprpc.NightLightMode.
        icon="mdi:lightbulb-night",
    ),
    PPRPCSelectEntityDescription(
        key="motion_sensitivity",
        translation_key="motion_sensitivity",
        entity_category=EntityCategory.CONFIG,
        options=_options(MotionSensitivity),
        enum=MotionSensitivity,
        property_key="motion_sensitivity",
        select_fn=lambda device: device.async_set_motion_sensitivity,
        icon="mdi:motion-sensor",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the PPRPC select platform."""
    device: PPRPCDevice = hass.data[DOMAIN][config_entry.unique_id]

    async_add_entities(PPRPCSelect(device, description) for description in SELECTS)


class PPRPCSelect(PPRPCBaseEntity, SelectEntity):
    """A camera mode exposed as a select."""

    entity_description: PPRPCSelectEntityDescription

    def __init__(
        self, device: PPRPCDevice, description: PPRPCSelectEntityDescription
    ) -> None:
        """Initialize the select."""
        super().__init__(device)
        self.entity_description = description
        self._attr_unique_id = f"{self.device.dev_id}_{description.key}"

    @property
    def current_option(self) -> str | None:
        """Return the last-known mode, None while the camera hasn't said.

        The raw value comes from the device's cached properties, refreshed at
        every connect and updated locally when an option is set from here.
        """
        value = self.device.properties.get(self.entity_description.property_key)
        try:
            return self.entity_description.enum(value).name.lower()
        except ValueError:
            # Includes None (never read) and values outside the documented
            # enum, which a new firmware could legitimately send.
            return None

    async def async_select_option(self, option: str) -> None:
        """Send the chosen mode to the camera."""
        member = self.entity_description.enum[option.upper()]
        await self.entity_description.select_fn(self.device)(member)
        self.async_write_ha_state()
