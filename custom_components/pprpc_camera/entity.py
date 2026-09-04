from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .device import PPRPCDevice


class PPRPCBaseEntity(Entity):
    """Base class common to all PPRPC entities."""

    # These entities push state via the dispatcher / are assumed-state; none of
    # them implement async_update, so polling would just be wasted no-op calls.
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device: PPRPCDevice) -> None:
        """Initialize the PPRPC entity."""
        self.device: PPRPCDevice = device

    async def async_added_to_hass(self) -> None:
        """Refresh state when the device's data or availability changes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.device.signal_available, self._handle_device_update
            )
        )

    @callback
    def _handle_device_update(self) -> None:
        """Handle a refresh of the device's properties or availability.

        Subclasses that cache state override this to adopt the new reading
        before writing; the signal also fires after each poll, not only on an
        availability change.
        """
        self.async_write_ha_state()

    @property
    def available(self):
        """Return True if device is available."""
        return self.device.available

    @property
    def device_info(self) -> DeviceInfo:
        """Return a device description for device registry."""
        props = self.device.properties
        info = DeviceInfo(
            identifiers={(DOMAIN, self.device.dev_id)},
            manufacturer="XC Things",
            # The device id encodes the model ("PPFT04...") and nothing else
            # names it; the SDK string is the closest thing to a firmware
            # version the camera reports.
            model="FT04" if str(self.device.dev_id).startswith("PPFT04") else None,
            serial_number=self.device.dev_id,
            sw_version=props.get("sdk") or None,
        )
        if props.get("mac"):
            info["connections"] = {(CONNECTION_NETWORK_MAC, props["mac"])}
        return info
