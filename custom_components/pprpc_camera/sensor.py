"""PPRPC diagnostic sensors (network, signal, clock drift).

These cameras never push updates, so values reflect the last-known device
properties (refreshed on every connect and by the consumer-counted polls)
and refresh on the availability signal.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, POLL_GROUP_INFO, POLL_GROUP_STATUS
from .device import PPRPCDevice
from .entity import PPRPCBaseEntity


@dataclass(frozen=True, kw_only=True)
class PPRPCSensorEntityDescription(SensorEntityDescription):
    """Describes a PPRPC sensor entity."""

    value_fn: Callable[[dict[str, Any]], Any]
    supported_fn: Callable[[dict[str, Any]], bool]
    # Which device poll group keeps this value fresh. None for values that
    # effectively never change, so they never cause a camera round trip.
    poll_group: str | None = None
    # Extra state attributes, for context that doesn't belong in the state.
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _clock_attrs(props: dict[str, Any]) -> dict[str, Any]:
    """The reading the drift was derived from, for context."""
    return {
        "camera_time": props.get("device_time"),
        "tz_offset_min": props.get("tz_offset_min"),
    }


SENSORS: tuple[PPRPCSensorEntityDescription, ...] = (
    PPRPCSensorEntityDescription(
        key="signal",
        translation_key="signal",
        poll_group=POLL_GROUP_STATUS,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda props: props.get("rssi"),
        supported_fn=lambda props: props.get("rssi") is not None,
    ),
    PPRPCSensorEntityDescription(
        key="ssid",
        translation_key="ssid",
        poll_group=POLL_GROUP_STATUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda props: props.get("ssid"),
        supported_fn=lambda props: bool(props.get("ssid")),
    ),
    PPRPCSensorEntityDescription(
        key="ip_address",
        translation_key="ip_address",
        poll_group=POLL_GROUP_INFO,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda props: props.get("ip"),
        supported_fn=lambda props: bool(props.get("ip")),
    ),
    PPRPCSensorEntityDescription(
        key="mac",
        translation_key="mac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        # No poll group -- a MAC address doesn't change.
        value_fn=lambda props: props.get("mac"),
        supported_fn=lambda props: bool(props.get("mac")),
    ),
    PPRPCSensorEntityDescription(
        key="clock_drift",
        translation_key="clock_drift",
        poll_group=POLL_GROUP_INFO,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        # How far the camera clock is ahead (+) or behind (-) this host at the
        # last reading. The offset answers the question the sensor exists for
        # ("is the clock right?") and stays put between readings, unlike a
        # locally-advanced timestamp would.
        value_fn=lambda props: props.get("device_time_drift_s"),
        supported_fn=lambda props: props.get("device_time_drift_s") is not None,
        attrs_fn=_clock_attrs,
    ),
    PPRPCSensorEntityDescription(
        key="sdk",
        translation_key="sdk",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        # No poll group -- the SDK string is fixed per firmware.
        value_fn=lambda props: props.get("sdk"),
        supported_fn=lambda props: bool(props.get("sdk")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the PPRPC sensor platform."""
    device: PPRPCDevice = hass.data[DOMAIN][config_entry.unique_id]
    props = device.properties
    async_add_entities(
        PPRPCSensor(device, description)
        for description in SENSORS
        if description.supported_fn(props)
    )


class PPRPCSensor(PPRPCBaseEntity, SensorEntity):
    """A PPRPC diagnostic sensor."""

    entity_description: PPRPCSensorEntityDescription

    def __init__(
        self, device: PPRPCDevice, description: PPRPCSensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(device)
        self.entity_description = description
        self._attr_unique_id = f"{self.device.dev_id}_{description.key}"

    async def async_added_to_hass(self) -> None:
        """Claim the poll group this sensor's value comes from.

        Only enabled entities are ever added, so claiming here is what keeps
        the camera from being polled for data nobody is displaying.
        """
        await super().async_added_to_hass()
        if self.entity_description.poll_group:
            self.async_on_remove(
                self.device.register_poll_group(self.entity_description.poll_group)
            )

    @property
    def native_value(self) -> Any:
        """Return the current value from the camera's last-known properties."""
        return self.entity_description.value_fn(self.device.properties)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return supporting context, if this sensor provides any."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.device.properties)
