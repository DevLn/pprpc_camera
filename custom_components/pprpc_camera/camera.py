"""The PPRPC camera entity: MJPEG stream plus PTZ/reboot/talk services."""

from __future__ import annotations

import asyncio
from functools import cached_property

import aiopprpc
import voluptuous as vol
from aiohttp import web
from homeassistant.components.camera import (
    Camera,
    CameraEntityDescription,
    CameraEntityFeature,
)
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import uuid

from .const import (
    ATTR_MEDIA,
    ATTR_PAN,
    ATTR_TILT,
    DIR_DOWN,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_UP,
    DOMAIN,
    LOGGER,
    SERVICE_PTZ,
    SERVICE_REBOOT,
    SERVICE_TALK,
)
from .device import PPRPCDevice
from .entity import PPRPCBaseEntity

# Value produced by the `media` selector in the talk service.
MEDIA_SELECTOR_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MEDIA_CONTENT_ID): cv.string,
        vol.Optional(ATTR_MEDIA_CONTENT_TYPE): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a PPRPC camera based on a config entry."""

    device: PPRPCDevice = hass.data[DOMAIN][config_entry.unique_id]

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_PTZ,
        {
            vol.Optional(ATTR_PAN): vol.In([DIR_LEFT, DIR_RIGHT]),
            vol.Optional(ATTR_TILT): vol.In([DIR_UP, DIR_DOWN]),
        },
        "async_perform_ptz",
    )
    platform.async_register_entity_service(
        SERVICE_REBOOT,
        None,
        "async_perform_reboot",
    )
    platform.async_register_entity_service(
        SERVICE_TALK,
        {vol.Required(ATTR_MEDIA): MEDIA_SELECTOR_SCHEMA},
        "async_perform_talk",
    )

    async_add_entities([PPRPCCamera(device)])


class PPRPCCamera(PPRPCBaseEntity, Camera):
    """An implementation of a PPRPC camera."""

    _attr_supported_features = CameraEntityFeature.ON_OFF
    description = CameraEntityDescription(key="camera", translation_key="camera")

    def __init__(self, device: PPRPCDevice) -> None:
        """Initialize a PPRPC camera."""
        PPRPCBaseEntity.__init__(self, device)
        Camera.__init__(self)

        self._attr_unique_id = f"{self.device.dev_id}_camera"
        # True while explicitly turned on via camera.turn_on, which holds a
        # connection reference open so streaming persists until turned off.
        self._stream_hold = False

    async def async_added_to_hass(self) -> None:
        """Subscribe to availability (base) and streaming-state updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.device.signal_streaming, self.async_write_ha_state
            )
        )

    @property
    def is_streaming(self) -> bool:
        """Return True only while video is actively being streamed."""
        dev = self.device.device
        return dev.is_connected and dev.is_video_requested

    async def async_turn_on(self) -> None:
        """Start streaming and keep the session open until turned off."""
        if not self._stream_hold:
            # Hold a connection reference so the session isn't idle-closed.
            await self.device.connect()
            self._stream_hold = True
        await self.device.device.start_video()

    async def async_turn_off(self) -> None:
        """Stop streaming and release the held session."""
        if self.device.device.is_connected and self.device.device.is_video_requested:
            await self.device.device.stop_video()
        if self._stream_hold:
            self._stream_hold = False
            await self.device.close()

    @cached_property
    def use_stream_for_stills(self) -> bool:
        """Whether to use stream to generate stills."""
        return False

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""
        LOGGER.debug("Getting camera image")
        async with self.device.ensure_connected():
            video_streaming = self.device.device.is_video_requested

            if not video_streaming:
                await self.device.device.start_video()
            image_frame = await self.device.device.get_video_frame()
            if not video_streaming:
                await self.device.device.stop_video()
        return image_frame and image_frame.data

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Generate an HTTP MJPEG stream from the camera."""
        async with self.device.ensure_connected():
            if not self.device.device.is_video_requested:
                await self.device.device.start_video()

            response = web.StreamResponse()
            boundary = "--frame" + uuid.random_uuid_hex()
            response.content_type = f"multipart/x-mixed-replace; boundary={boundary}"
            response.content_length = 1000000000000
            await response.prepare(request)

            try:
                while True:
                    try:
                        frame = await asyncio.wait_for(
                            self.device.device.get_video_frame(), timeout=10
                        )
                    except asyncio.TimeoutError:
                        LOGGER.warning("Error getting video frame: Timeout")
                        break
                    except aiopprpc.NotConnectedError as err:
                        LOGGER.warning("Error getting video frame: %s", err)
                        break
                    if not frame:
                        LOGGER.warning("Error getting video frame: empty frame")
                        break
                    header = f"--{boundary}\r\n".encode()
                    header += b"Content-Length: %d\r\n" % len(frame.data)
                    header += b"Content-Type: image/jpeg\r\n\r\n"

                    try:
                        await response.write(header)
                        await response.write(frame.data)
                    except (TimeoutError, ConnectionResetError):
                        break
            finally:
                LOGGER.debug("%s camera stream closed", self.name)
        # Return outside the `finally` so a CancelledError raised when the
        # client disconnects propagates instead of being swallowed by `return`.
        return response

    async def async_perform_ptz(self, pan=None, tilt=None) -> None:
        """Perform a PTZ step on the camera.

        pan and tilt are independent axes; apply both when both are given.
        Each is a bounded step (PtzCtrl moves continuously until stopped, so
        the device helper issues move + wait + stop).
        """
        if pan:
            await self.device.async_ptz_step(pan.lower())
        if tilt:
            await self.device.async_ptz_step(tilt.lower())

    async def async_perform_reboot(self) -> None:
        """Reboot the camera."""
        # Go through the device helper so the session is (re)connected if it
        # was idle-closed.
        await self.device.async_reboot()

    async def async_perform_talk(self, media: dict) -> None:
        """Play a media/TTS source to the camera speaker (talk-back)."""
        from homeassistant.components import media_source

        media_id = media[ATTR_MEDIA_CONTENT_ID]
        if media_source.is_media_source_id(media_id):
            resolved = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = resolved.url
        # media_source hands back a signed but *relative* URL
        # ("/media/local/x.mp3?authSig=..."); ffmpeg needs an absolute one.
        media_id = async_process_play_media_url(self.hass, media_id)
        await self.device.async_talk(media_id)
