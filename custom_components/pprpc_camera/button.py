"""PPRPC buttons: reboot, time sync, and the flashlight.

The flashlight is a button by design, not a stripped-down switch: IoT cid
201 is a stateless toggle on the wire. Any message addressed to it flips the
light -- the payload is ignored, and even a value-less "read" actuates it --
so there is no state to display and no safe way to query one. A press is the
only honest representation.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import PPRPCDevice
from .entity import PPRPCBaseEntity


@dataclass(frozen=True, kw_only=True)
class PPRPCButtonEntityDescription(ButtonEntityDescription):
    """Describes a PPRPC button entity."""

    press_fn: Callable[[PPRPCDevice], Callable[[], Coroutine[Any, Any, None]]]


BUTTONS: tuple[PPRPCButtonEntityDescription, ...] = (
    PPRPCButtonEntityDescription(
        key="flashlight",
        translation_key="flashlight",
        press_fn=lambda device: device.async_toggle_flashlight,
        icon="mdi:flashlight",
    ),
    PPRPCButtonEntityDescription(
        key="reboot",
        translation_key="reboot",
        press_fn=lambda device: device.async_reboot,
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
    ),
    PPRPCButtonEntityDescription(
        key="sync_time",
        translation_key="sync_time",
        press_fn=lambda device: device.async_sync_time,
        icon="mdi:clock-check",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the PPRPC button platform."""
    device = hass.data[DOMAIN][config_entry.unique_id]

    async_add_entities(PPRPCButton(device, description) for description in BUTTONS)


class PPRPCButton(PPRPCBaseEntity, ButtonEntity):
    """A PPRPC button."""

    entity_description: PPRPCButtonEntityDescription

    def __init__(
        self, device: PPRPCDevice, description: PPRPCButtonEntityDescription
    ) -> None:
        """Initialize the button."""
        super().__init__(device)

        self._attr_unique_id = f"{self.device.dev_id}_{description.key}"
        self.entity_description = description

    async def async_press(self) -> None:
        """Handle button press."""
        await self.entity_description.press_fn(self.device)()
