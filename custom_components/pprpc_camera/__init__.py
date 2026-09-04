"""The PPRPC IP Camera integration (XC Things FT04 and friends, fully local)."""

import voluptuous as vol
from aiopprpc import AuthError, NotConnectedError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DISCOVERY,
    CONF_ENABLED,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from .config_helpers import get_config
from .const import (
    CONF_DURATION,
    CONF_IDLE_DISCONNECT_DELAY,
    CONF_INFO_POLL_INTERVAL,
    CONF_INTERVAL,
    CONF_IP,
    CONF_STATUS_POLL_INTERVAL,
    DEFAULT_IDLE_DISCONNECT_DELAY,
    DEFAULT_INFO_POLL_INTERVAL,
    DEFAULT_STATUS_POLL_INTERVAL,
    DOMAIN,
    LOGGER,
    PLATFORMS,
)
from .device import PPRPCDevice
from .discovery import async_start_discovery

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_DISCOVERY, default={}): vol.Schema(
                    {
                        vol.Optional(CONF_ENABLED, default=True): cv.boolean,
                        vol.Optional(CONF_DURATION, default=10): cv.positive_int,
                        vol.Optional(CONF_INTERVAL, default=600): cv.positive_int,
                        vol.Optional(CONF_IP): vol.Any(cv.string, [cv.string]),
                    }
                ),
                vol.Optional(
                    CONF_IDLE_DISCONNECT_DELAY,
                    default=DEFAULT_IDLE_DISCONNECT_DELAY,
                ): vol.All(vol.Coerce(int), vol.Range(min=0)),
                vol.Optional(
                    CONF_STATUS_POLL_INTERVAL,
                    default=DEFAULT_STATUS_POLL_INTERVAL,
                ): vol.All(vol.Coerce(int), vol.Range(min=0)),
                vol.Optional(
                    CONF_INFO_POLL_INTERVAL,
                    default=DEFAULT_INFO_POLL_INTERVAL,
                ): vol.All(vol.Coerce(int), vol.Range(min=0)),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)
"""
configuration.yaml example:

pprpc_camera:
    discovery:
        enabled: true
        duration: 10    # seconds to listen for devices during each discovery
        interval: 600   # seconds between discovery attempts
        ip:             # list of IPs to limit discovery to
            - 192.168.1.255
        # or a single IP (usually a broadcast address)
        ip: 192.168.1.255
        # if 'ip' is not specified, discovery broadcasts on all interfaces
    idle_disconnect_delay: 5    # seconds to keep a session warm after the last
                                # operation (0 = disconnect immediately)
    status_poll_interval: 300   # seconds between Wi-Fi signal refreshes
                                # (0 = never poll)
    info_poll_interval: 3600    # seconds between clock/network refreshes
                                # (0 = never poll)

All of these can also be set per camera in the integration's options, which
takes precedence over the values here. There are no credentials: the LanAuth
handshake uses a single-use token from UDP discovery.
"""


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    # init storage for registries
    hass.data[DOMAIN] = {}

    # load optional global config
    cfg = config if DOMAIN in config else CONFIG_SCHEMA({DOMAIN: {}})
    hass.data[DOMAIN]["config"] = cfg[DOMAIN]
    LOGGER.debug("Config: %s", get_config(hass))

    await async_start_discovery(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    device = PPRPCDevice(hass, config_entry)
    try:
        await device.async_setup()
    except (TimeoutError, NotConnectedError, AuthError) as err:
        # AuthError usually means the discovery-fresh lsign expired between
        # discovery and LanAuth (or another client holds the camera -- only one
        # may connect); both are transient, so retry rather than fail.
        if getattr(device, "device", None) is not None:
            await device.device.close()
        raise ConfigEntryNotReady(
            f"Could not connect to camera {device.host}: {err}"
        ) from err

    hass.data[DOMAIN][config_entry.unique_id] = device

    device.platforms = [
        Platform.CAMERA,
        Platform.BUTTON,
        Platform.SWITCH,
        Platform.SELECT,
        Platform.SENSOR,
        Platform.BINARY_SENSOR,
    ]

    await hass.config_entries.async_forward_entry_setups(config_entry, device.platforms)

    # PPRPCDevice.async_setup() already registers an options-update listener
    # that reloads the entry, so don't register a second one here.
    config_entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, device.async_stop)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    device: PPRPCDevice | None = hass.data.get(DOMAIN, {}).get(entry.unique_id)
    # Unload the platforms that were actually set up, not just PLATFORMS
    # (camera only) -- otherwise the other entities are orphaned on reload.
    platforms = device.platforms if device and device.platforms else PLATFORMS
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded and device is not None:
        # Tear the warm session down and drop the reference so nothing leaks
        # across reloads.
        await device.async_stop()
        hass.data[DOMAIN].pop(entry.unique_id, None)
    return unloaded
