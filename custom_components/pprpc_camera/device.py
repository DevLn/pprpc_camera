"""PPRPC device abstraction.

Mirrors the sibling pppp_camera integration: a refcounted connect()/close()
pair with idle-deferred teardown, an ensure_connected() context manager every
command runs inside, and consumer-counted poll loops instead of a
DataUpdateCoordinator.

One PPRPC-specific wrinkle: every connect performs a fresh UDP discovery,
because the LanAuth credential (lsign) is minted per discovery reply and
expires. aiopprpc does that internally -- but it makes each connect a little
more expensive, which is one more reason the idle window and the
consumer-counted polls exist.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Callable

import aiopprpc
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .config_helpers import (
    get_idle_disconnect_delay,
    get_info_poll_interval,
    get_status_poll_interval,
)
from .const import (
    DOMAIN,
    LOGGER,
    POLL_GROUP_INFO,
    POLL_GROUP_STATUS,
)


class PPRPCDevice:
    """Manages one PPRPC camera."""

    device: aiopprpc.Device

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the device."""
        self.hass: HomeAssistant = hass
        self.config_entry: ConfigEntry = config_entry
        self.available: bool = True
        self.platforms: list[Platform] = []

        self._connected_num = 0

        # Connection lifecycle: serialize connect/close and keep the session
        # warm for a short idle window so back-to-back operations reuse it.
        self._lock = asyncio.Lock()
        self._idle_unload_task: asyncio.Task | None = None
        self._idle_disconnect_delay: int = get_idle_disconnect_delay(hass, config_entry)
        self._status_poll_interval: int = get_status_poll_interval(hass, config_entry)
        self._info_poll_interval: int = get_info_poll_interval(hass, config_entry)
        self._poll_tasks: list[asyncio.Task] = []
        # Live entity count per poll group; a group with none is never polled.
        self._poll_consumers: dict[str, int] = {}

        # Entities subscribe to these signals to refresh availability / stream
        # state / pushed events.
        self.signal_available = f"{DOMAIN}_{config_entry.entry_id}_available"
        self.signal_streaming = f"{DOMAIN}_{config_entry.entry_id}_streaming"
        self.signal_event = f"{DOMAIN}_{config_entry.entry_id}_event"

    def _set_available(self, value: bool) -> None:
        """Update availability and notify entities only when it changes."""
        if self.available != value:
            self.available = value
            async_dispatcher_send(self.hass, self.signal_available)

    @callback
    def _on_video_state_change(self, is_streaming: bool) -> None:
        """Forward the library's streaming-state change to subscribed entities."""
        async_dispatcher_send(self.hass, self.signal_streaming)

    def _on_event(self, name: str, payload: dict) -> None:
        """Forward a pushed camera event (cmd 13) to subscribed entities.

        Called by aiopprpc from within the event loop. Motion push has only
        ever been captured on the vendor's cloud notify channel; whether the
        camera sends it on a LAN-direct session is unverified, so this may
        simply never fire -- the motion binary sensor documents that.
        """
        async_dispatcher_send(self.hass, self.signal_event, name, payload)

    @property
    def host(self) -> str:
        """Return the host of this device."""
        return self.config_entry.options[CONF_HOST]

    @property
    def dev_id(self) -> str:
        """Return the device id (e.g. "PPFT04...") of this device."""
        # The descriptor only exists after the first successful discovery;
        # fall back to the entry's unique_id, which is the same value.
        return self.device.dev_id or self.config_entry.unique_id

    @property
    def properties(self) -> dict:
        """Last-known device properties (NetInfo, Wi-Fi status, modes, clock).

        aiopprpc refreshes these on every connect; between sessions the
        last-known values remain readable, which is what the entities want --
        these cameras never push updates.
        """
        return self.device.properties

    async def connect(self):
        """Connect to the device, reusing a warm session when available."""
        async with self._lock:
            # A new user cancels any pending idle teardown and reuses the session.
            self._cancel_idle_unload()
            self._connected_num += 1
            if not self.device.is_connected:
                try:
                    await self.device.connect()
                except Exception:
                    # ensure_connected() skips close() when connect() raises, so
                    # roll back the reference we just took to avoid leaking it.
                    self._connected_num -= 1
                    self._set_available(False)
                    raise
            self._set_available(True)

    async def close(self):
        """Release a connection reference; tear down only after an idle window."""
        async with self._lock:
            if not self._connected_num:
                return
            self._connected_num -= 1
            if self._connected_num == 0:
                # Defer teardown instead of closing inline. A command arriving
                # within the idle window reuses the live session, and a
                # fire-and-forget command is not cut off by an immediate close.
                self._cancel_idle_unload()
                self._idle_unload_task = asyncio.create_task(self._idle_unload())

    async def _idle_unload(self) -> None:
        """Close the session once it has been idle for the configured delay."""
        try:
            await asyncio.sleep(self._idle_disconnect_delay)
            async with self._lock:
                # Re-check under the lock: a user may have reconnected during
                # the wait.
                if self._connected_num == 0 and self.device.is_connected:
                    await self.device.close()
                self._idle_unload_task = None
        except asyncio.CancelledError:
            # Cancellation can arrive during the sleep or while awaiting the
            # lock; either way there is nothing to clean up (a reconnect took
            # over).
            return

    def _cancel_idle_unload(self) -> None:
        """Cancel a pending idle teardown, if any."""
        if self._idle_unload_task and not self._idle_unload_task.done():
            self._idle_unload_task.cancel()
        self._idle_unload_task = None

    @contextlib.asynccontextmanager
    async def ensure_connected(self):
        """Ensure the device is connected for the duration of the block."""
        await self.connect()
        try:
            yield self
        finally:
            await self.close()

    async def async_setup(self) -> None:
        """Set up the device."""
        self.device = aiopprpc.Device(
            self.config_entry.options[CONF_HOST],
            on_video_state_change=self._on_video_state_change,
            on_event=self._on_event,
        )

        # Connecting populates Device.properties (NetInfo, Wi-Fi status, flip,
        # IR-cut, motion sensitivity, night light, clock) via the session's
        # setup_device(); nothing further to fetch here.
        async with self.ensure_connected():
            pass

        self._start_polling()
        self.config_entry.async_on_unload(self._stop_polling)

    async def async_stop(self, event=None):
        """Shut it all down."""
        self._stop_polling()
        async with self._lock:
            self._cancel_idle_unload()
            self._connected_num = 0
            await self.device.close()

    # --- polling ----------------------------------------------------------
    async def async_refresh_status(self) -> None:
        """Re-read the Wi-Fi status (RSSI, current SSID).

        The camera never pushes updates and the library reads these once per
        connect, so a long-held session (e.g. while streaming) would otherwise
        show a frozen signal reading.
        """
        async with self.ensure_connected():
            status = await self.device.get_wifi_status()
            if status.get("rssi") is not None:
                self.properties["rssi"] = status["rssi"]
            if status.get("ssid"):
                self.properties["ssid"] = status["ssid"]
        async_dispatcher_send(self.hass, self.signal_available)

    async def async_refresh_extra_info(self) -> None:
        """Re-read slow-moving values (clock drift, network info)."""
        async with self.ensure_connected():
            try:
                clock = await self.device.get_time()
                self.properties["device_time"] = clock.get("local")
                self.properties["device_time_drift_s"] = clock.get("drift_s")
                self.properties["tz_offset_min"] = clock.get("tz_offset_min")
            except aiopprpc.PPRPCError as err:
                LOGGER.debug("%s: time unavailable: %s", self.dev_id, err)
            try:
                net = await self.device.get_network_info()
                self.properties.update({k: v for k, v in net.items() if v})
            except aiopprpc.PPRPCError as err:
                LOGGER.debug("%s: network info unavailable: %s", self.dev_id, err)
        async_dispatcher_send(self.hass, self.signal_available)

    @callback
    def register_poll_group(self, group: str) -> Callable[[], None]:
        """Declare that a live entity reads from `group`, and return the
        function that releases it again.

        Home Assistant never adds disabled entities, so counting registrations
        is enough to know whether anything actually needs the data; disabling
        every entity of a group stops that poll (and its camera round trip)
        entirely.
        """
        self._poll_consumers[group] = self._poll_consumers.get(group, 0) + 1

        @callback
        def release() -> None:
            self._poll_consumers[group] = max(0, self._poll_consumers.get(group, 0) - 1)

        return release

    async def _poll_loop(self, group: str, interval: int, refresh) -> None:
        """Refresh `group` every `interval` seconds while it has consumers."""
        while True:
            try:
                await asyncio.sleep(interval)
                if not self._poll_consumers.get(group):
                    # Nothing is using this data; skip the round trip entirely
                    # rather than waking the camera for values nobody reads.
                    continue
                await refresh()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - a poll failure is not fatal
                # An unreachable camera is already reflected by availability;
                # keep polling so the values recover on their own.
                LOGGER.debug("%s: %s poll failed: %s", self.dev_id, group, err)

    def _start_polling(self) -> None:
        for group, interval, refresh in (
            (POLL_GROUP_STATUS, self._status_poll_interval, self.async_refresh_status),
            (POLL_GROUP_INFO, self._info_poll_interval, self.async_refresh_extra_info),
        ):
            if not interval:
                LOGGER.debug("%s: %s polling disabled", self.dev_id, group)
                continue
            self._poll_tasks.append(
                self.hass.async_create_background_task(
                    self._poll_loop(group, interval, refresh),
                    f"{DOMAIN}_{group}_poll_{self.dev_id}",
                )
            )

    def _stop_polling(self) -> None:
        for task in self._poll_tasks:
            if not task.done():
                task.cancel()
        self._poll_tasks = []

    # --- commands ---------------------------------------------------------
    async def async_reboot(self, data=None) -> None:
        """Send a Reboot command."""
        async with self.ensure_connected():
            await self.device.reboot()

    async def async_toggle_flashlight(self, data=None) -> None:
        """Flip the white flashlight.

        Datapoint 201 is a stateless toggle: any message addressed to it flips
        the light, the payload is ignored, and even a value-less "read"
        actuates it. There is no state to query, which is why this is a button
        press and not a switch.
        """
        async with self.ensure_connected():
            await self.device.toggle_flashlight()

    async def async_set_status_led(self, on: bool) -> None:
        """Turn the status indicator LED on or off (write-only, no getter)."""
        async with self.ensure_connected():
            await self.device.set_status_led(
                aiopprpc.StatusLed.ON if on else aiopprpc.StatusLed.OFF
            )

    async def async_set_flip(self, mode: aiopprpc.FlipMode) -> None:
        """Set the image orientation and remember the new value."""
        async with self.ensure_connected():
            await self.device.set_flip(mode)
        self.properties["flip"] = int(mode)

    async def async_set_ircut(self, mode: aiopprpc.IRCutMode) -> None:
        """Set the night-vision (IR-cut) mode and remember the new value."""
        async with self.ensure_connected():
            await self.device.set_ircut(mode)
        self.properties["ircut"] = int(mode)

    async def async_set_night_light(self, mode: aiopprpc.NightLightMode) -> None:
        """Set the night-light mode and remember the new value."""
        async with self.ensure_connected():
            await self.device.set_night_light(mode)
        self.properties["night_light"] = int(mode)

    async def async_set_motion_sensitivity(
        self, level: aiopprpc.MotionSensitivity
    ) -> None:
        """Set the motion sensitivity and remember the new value."""
        async with self.ensure_connected():
            await self.device.set_motion_sensitivity(level)
        self.properties["motion_sensitivity"] = int(level)

    async def async_ptz_step(self, direction: str) -> None:
        """Move one bounded step in `direction` ('up'/'down'/'left'/'right').

        PtzCtrl(2664) moves continuously until told to stop, so the library's
        ptz_step() (move, wait, stop) is what a service call wants.
        """
        async with self.ensure_connected():
            await self.device.ptz_step(direction)

    async def async_sync_time(self, data=None) -> None:
        """Set the camera clock to Home Assistant's local time."""
        async with self.ensure_connected():
            await self.device.set_time(dt_util.now())
            # Re-read so the clock-drift sensor reflects the sync immediately
            # instead of showing the pre-sync drift until the next info poll.
            try:
                clock = await self.device.get_time()
                self.properties["device_time"] = clock.get("local")
                self.properties["device_time_drift_s"] = clock.get("drift_s")
                self.properties["tz_offset_min"] = clock.get("tz_offset_min")
            except aiopprpc.PPRPCError as err:
                LOGGER.debug("%s: clock read-back after sync failed: %s",
                             self.dev_id, err)
        async_dispatcher_send(self.hass, self.signal_available)

    async def async_talk(self, url: str) -> None:
        """Play an audio URL to the camera speaker (talk-back).

        The camera wants raw 16-bit PCM at 8 kHz mono; ffmpeg transcodes
        arbitrary media to that and the session frames/encrypts each chunk.
        Chunks are paced in real time so the camera's small jitter buffer
        isn't flooded.
        """
        from homeassistant.components.ffmpeg import get_ffmpeg_manager

        async with self.ensure_connected():
            device = self.device

            # Open the uplink before spawning anything: a camera that refuses
            # (SpeakStart timing out or erroring, session lost) leaves nothing
            # to clean up this way. Spawning ffmpeg first leaked the process
            # and its stderr drain whenever start_talk() failed.
            try:
                await device.start_talk()
            except aiopprpc.PPRPCError as err:
                raise HomeAssistantError(
                    f"Could not open talk-back on the camera: {err}"
                ) from err

            # Drain stderr concurrently, keeping only the tail. ffmpeg blocks
            # once the pipe buffer fills, and when it can't fetch the URL its
            # diagnostics are the only clue -- discarding them turns every
            # failure into silence with no error at all.
            stderr_tail: deque[str] = deque(maxlen=15)
            proc = None
            drain = None
            sent = 0
            try:
                ffmpeg = get_ffmpeg_manager(self.hass)
                proc = await asyncio.create_subprocess_exec(
                    ffmpeg.binary, "-nostdin", "-i", url,
                    "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "8000",
                    "-ac", "1", "pipe:1",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )

                async def _drain_stderr() -> None:
                    async for line in proc.stderr:
                        stderr_tail.append(line.decode("utf-8", "replace").strip())

                drain = self.hass.async_create_task(_drain_stderr())

                # 960 samples * 2 bytes = 120 ms per chunk at 8 kHz; the
                # session re-frames internally to the camera's own 40 ms frames.
                chunk_bytes = 1920
                chunk_seconds = 0.12
                while True:
                    pcm = await proc.stdout.read(chunk_bytes)
                    if not pcm:
                        break
                    await device.send_audio(pcm)
                    sent += len(pcm)
                    # Pace by how much audio this chunk actually represents.
                    await asyncio.sleep(chunk_seconds * len(pcm) / chunk_bytes)
            finally:
                # The session may have died mid-stream; a failing SpeakStop
                # must not mask the original error or skip the ffmpeg cleanup.
                with contextlib.suppress(aiopprpc.PPRPCError):
                    await device.stop_talk()
                if proc is not None and proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.terminate()
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(proc.wait(), timeout=5)
                if drain is not None:
                    drain.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await drain

            if not sent:
                # ffmpeg produced no audio: bad URL, unreachable HA base URL,
                # unsupported container. Surface its own words rather than
                # letting the service silently succeed.
                detail = "; ".join(stderr_tail) or "no output from ffmpeg"
                raise HomeAssistantError(f"Could not decode audio from {url}: {detail}")
            LOGGER.debug(
                "%s: talk-back sent %.1f s of audio", self.dev_id, sent / 16000
            )
