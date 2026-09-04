"""PPRPC motion binary sensor (scaffolded -- read the caveat below).

Motion push (cmd 13) has only ever been captured on the vendor's CLOUD notify
channel. Whether the camera pushes motion events on a LAN-direct session is
UNVERIFIED -- and even if it does, events can only arrive while a session is
open (streaming, or within the idle window after a command). The parser and
callback plumbing are wired all the way through aiopprpc, so if the camera
does push locally this sensor works; until someone observes that on hardware,
it is disabled by default rather than shipping a motion sensor that reads a
confident (and possibly meaningless) "clear".
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later

from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER, MOTION_OFF_DELAY
from .device import PPRPCDevice
from .entity import PPRPCBaseEntity

MOTION_DESCRIPTION = BinarySensorEntityDescription(
    key="motion",
    translation_key="motion",
    device_class=BinarySensorDeviceClass.MOTION,
    # Disabled by default: LAN-direct motion push is unverified (see module
    # docstring). Enable it to test on real hardware.
    entity_registry_enabled_default=False,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the PPRPC binary sensor platform."""
    device: PPRPCDevice = hass.data[DOMAIN][config_entry.unique_id]

    async_add_entities([PPRPCMotionSensor(device)])


class PPRPCMotionSensor(PPRPCBaseEntity, BinarySensorEntity):
    """Motion, driven by pushed camera events.

    The camera sends a single event per detection and no "motion ended", so
    the off-edge is synthesized after MOTION_OFF_DELAY seconds.
    """

    _attr_is_on = False

    def __init__(self, device: PPRPCDevice) -> None:
        """Initialize the sensor."""
        super().__init__(device)
        self.entity_description = MOTION_DESCRIPTION
        self._attr_unique_id = f"{self.device.dev_id}_motion"
        self._cancel_off = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to pushed camera events."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.device.signal_event, self._handle_event
            )
        )
        self.async_on_remove(self._cancel_off_timer)

    @callback
    def _cancel_off_timer(self) -> None:
        if self._cancel_off is not None:
            self._cancel_off()
            self._cancel_off = None

    @callback
    def _handle_event(self, name: str, payload: dict) -> None:
        """Handle a pushed camera event."""
        if name != "motion":
            return
        LOGGER.debug("%s: motion event: %s", self.device.dev_id, payload)
        self._attr_is_on = True
        self.async_write_ha_state()

        self._cancel_off_timer()
        self._cancel_off = async_call_later(self.hass, MOTION_OFF_DELAY, self._clear)

    @callback
    def _clear(self, _now) -> None:
        """Synthesize the off-edge once the hold time expires."""
        self._cancel_off = None
        self._attr_is_on = False
        self.async_write_ha_state()
