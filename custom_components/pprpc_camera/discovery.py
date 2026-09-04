"""Background UDP discovery for PPRPC cameras."""

import asyncio
from ipaddress import ip_address, ip_network
from typing import List

from aiopprpc import DeviceDescriptor, Discovery
from homeassistant.components import network
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_ENABLED,
    CONF_HOST,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.discovery_flow import async_create_flow

from .config_helpers import get_discovery_config
from .const import CONF_DURATION, CONF_INTERVAL, CONF_IP, DOMAIN, LOGGER


async def async_start_discovery(hass: HomeAssistant) -> None:
    """Start the background discovery process."""

    discovery_config = get_discovery_config(hass)
    enabled = discovery_config.get(CONF_ENABLED, True)
    interval = discovery_config.get(CONF_INTERVAL, 600)

    if not enabled:
        LOGGER.info("PPRPC camera discovery is disabled in configuration")
        return

    # A single, reused instance so the "already discovered" set persists across
    # iterations; a fresh instance each pass would re-raise a discovery flow
    # for every known camera on every interval.
    discovery = PPRPCDiscovery(hass)

    async def discovery_loop() -> None:
        """Run discovery loop indefinitely."""
        while True:
            try:
                await discovery.async_run_discovery()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep the loop alive
                LOGGER.error("Error during PPRPC camera discovery: %s", err)
            await asyncio.sleep(interval)

    # Track the task so it can be cancelled on shutdown instead of running
    # forever detached.
    task = hass.async_create_background_task(
        discovery_loop(), name="pprpc_camera discovery"
    )
    hass.data.setdefault(DOMAIN, {})["_discovery_task"] = task

    @callback
    def _stop_discovery(_event) -> None:
        if not task.done():
            task.cancel()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_discovery)


class PPRPCDiscovery:
    """Class to manage PPRPC camera discovery."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the discovery class."""
        self.hass = hass
        self.discovered_devices = set[str]()
        # Only warn once about a misconfiguration, not every interval.
        self._warned_no_ips = False

    async def async_run_discovery(self) -> None:
        """Run one PPRPC camera discovery pass."""

        discovery_config = get_discovery_config(self.hass)
        duration = discovery_config.get(CONF_DURATION, 10)
        custom_ips = discovery_config.get(CONF_IP)

        discovery_ips = (
            self._get_custom_ips(custom_ips)
            if custom_ips
            else await self._async_get_broadcast_ips()
        )

        if not discovery_ips:
            if not self._warned_no_ips:
                LOGGER.warning(
                    "No discovery IPs found, PPRPC camera discovery will not run."
                )
                self._warned_no_ips = True
            return
        self._warned_no_ips = False

        def device_callback(device: DeviceDescriptor):
            self._discovered_device_callback(device.addr, device.dev_id)

        for ip in discovery_ips:
            LOGGER.debug("Starting PPRPC discovery on IP: %s", ip)

            discovery = Discovery(ip)
            task = asyncio.create_task(discovery.discover(device_callback, period=3))
            try:
                await asyncio.sleep(duration)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    LOGGER.debug("Discovery task for IP %s cancelled", ip)

    def _discovered_device_callback(self, host: str, device_id: str) -> None:
        """Handle a discovered PPRPC camera."""

        if device_id in self.discovered_devices:
            LOGGER.debug("Already discovered device ID %s, ignoring.", device_id)
            return

        LOGGER.info("Discovered PPRPC camera at %s with device ID: %s", host, device_id)

        discovery_info = {
            CONF_HOST: host,
            CONF_DEVICE_ID: device_id,
        }

        async_create_flow(
            self.hass,
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data=discovery_info,
        )

        self.discovered_devices.add(device_id)

    def _get_custom_ips(self, custom_ips) -> List[str]:
        """Process custom IP configuration into a list of valid IPs."""

        if isinstance(custom_ips, str):
            custom_ips = [custom_ips]

        def is_valid_ip(ip_config: str) -> bool:
            try:
                ip_address(ip_config)
                return True
            except ValueError:
                LOGGER.warning(
                    "Invalid IP address in discovery configuration: %s", ip_config
                )
                return False

        valid_ips = [ip for ip in custom_ips if is_valid_ip(ip)]

        if not valid_ips:
            LOGGER.error(
                "No valid IP addresses provided in configuration: %s", custom_ips
            )
            return []

        LOGGER.debug("Using %d custom discovery IPs: %s", len(valid_ips), valid_ips)
        return valid_ips

    async def _async_get_broadcast_ips(self) -> List[str]:
        """Get broadcast IPs for all enabled HA network adapters."""
        broadcast_ips = []

        try:
            adapters = await network.async_get_adapters(self.hass)

            for adapter in adapters:
                if not adapter.get("enabled", False):
                    continue

                # IPv4 only: broadcast is an IPv4 concept.
                for ip_info in adapter.get("ipv4", []):
                    local_ip = ip_info["address"]
                    network_prefix = ip_info["network_prefix"]

                    try:
                        ip_net = ip_network(
                            f"{local_ip}/{network_prefix}", strict=False
                        )
                        broadcast_ip = str(ip_net.broadcast_address)

                        if broadcast_ip not in broadcast_ips:
                            broadcast_ips.append(broadcast_ip)
                            LOGGER.debug(
                                "Found broadcast IP %s for adapter %s (%s/%s)",
                                broadcast_ip,
                                adapter["name"],
                                local_ip,
                                network_prefix,
                            )

                    except ValueError as err:
                        LOGGER.warning(
                            "Failed to calculate broadcast IP for %s/%s on adapter %s: %s",
                            local_ip,
                            network_prefix,
                            adapter["name"],
                            err,
                        )

        except Exception as err:  # noqa: BLE001 - diagnostics only
            LOGGER.error("Failed to get network adapters: %s", err)
            return []

        if not broadcast_ips:
            LOGGER.error("No broadcast IPs found on any network adapters.")
            return []

        LOGGER.debug("Found %d broadcast IPs: %s", len(broadcast_ips), broadcast_ips)
        return broadcast_ips
