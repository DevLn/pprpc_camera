"""Helpers for configuration handling."""

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DISCOVERY
from homeassistant.core import HomeAssistant

from .const import (
    CONF_IDLE_DISCONNECT_DELAY,
    CONF_INFO_POLL_INTERVAL,
    CONF_STATUS_POLL_INTERVAL,
    DEFAULT_IDLE_DISCONNECT_DELAY,
    DEFAULT_INFO_POLL_INTERVAL,
    DEFAULT_STATUS_POLL_INTERVAL,
    DOMAIN,
)


def get_config(hass: HomeAssistant) -> dict[str, Any]:
    """Get the YAML configuration for DOMAIN."""
    return hass.data.get(DOMAIN, {}).get("config", {})


def get_discovery_config(hass: HomeAssistant) -> dict[str, Any]:
    """Get the discovery section of the YAML configuration."""
    return get_config(hass).get(CONF_DISCOVERY, {})


def _get_interval(
    hass: HomeAssistant,
    config_entry: ConfigEntry | None,
    option: str,
    default: int,
) -> int:
    """Read an interval option, preferring the per-entry value over YAML."""
    if config_entry is not None and option in config_entry.options:
        return int(config_entry.options[option])
    return int(get_config(hass).get(option, default))


def get_idle_disconnect_delay(
    hass: HomeAssistant, config_entry: ConfigEntry | None = None
) -> int:
    """Seconds to keep a camera session warm after the last operation."""
    return _get_interval(
        hass, config_entry, CONF_IDLE_DISCONNECT_DELAY, DEFAULT_IDLE_DISCONNECT_DELAY
    )


def get_status_poll_interval(
    hass: HomeAssistant, config_entry: ConfigEntry | None = None
) -> int:
    """Seconds between Wi-Fi status refreshes (0 disables polling)."""
    return _get_interval(
        hass, config_entry, CONF_STATUS_POLL_INTERVAL, DEFAULT_STATUS_POLL_INTERVAL
    )


def get_info_poll_interval(
    hass: HomeAssistant, config_entry: ConfigEntry | None = None
) -> int:
    """Seconds between clock/network-info refreshes (0 disables polling)."""
    return _get_interval(
        hass, config_entry, CONF_INFO_POLL_INTERVAL, DEFAULT_INFO_POLL_INTERVAL
    )
