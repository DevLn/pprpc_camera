import logging
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "pprpc_camera"
PLATFORMS: Final = [Platform.CAMERA]

LOGGER = logging.getLogger(__package__)

ATTR_PAN = "pan"
ATTR_TILT = "tilt"
ATTR_MEDIA = "media"

DIR_UP = "UP"
DIR_DOWN = "DOWN"
DIR_LEFT = "LEFT"
DIR_RIGHT = "RIGHT"

SERVICE_PTZ = "ptz"
SERVICE_REBOOT = "reboot"
SERVICE_TALK = "talk"

SOURCE_DISCOVERY_CONFIRM = "discovery_confirm"

CONF_IP = "ip"
CONF_DURATION = "duration"
CONF_INTERVAL = "interval"
CONF_IDLE_DISCONNECT_DELAY = "idle_disconnect_delay"
CONF_STATUS_POLL_INTERVAL = "status_poll_interval"
CONF_INFO_POLL_INTERVAL = "info_poll_interval"

# Poll groups. Entities register the group they read from when they are added
# to Home Assistant, and a group is only polled while something is actually
# using it -- disabling every sensor of a group stops that poll entirely.
POLL_GROUP_STATUS = "status"
POLL_GROUP_INFO = "info"

# Seconds to keep a camera session open after the last in-flight operation
# finishes. Keeping it warm lets back-to-back commands (e.g. PTZ bursts) reuse
# the session and avoids tearing the connection down before a fire-and-forget
# command has been delivered. 0 disconnects immediately. Note the camera itself
# drops connections that go quiet, so the library keeps heartbeats running for
# as long as the session is held open.
DEFAULT_IDLE_DISCONNECT_DELAY = 5

# How often to re-read the Wi-Fi status block (RSSI, current SSID). The camera
# never pushes updates, so without this the signal reading stays frozen at
# whatever it was when the session first connected. Every poll opens a full
# session (discovery + LanAuth), so keep it infrequent. 0 disables polling.
DEFAULT_STATUS_POLL_INTERVAL = 300

# How often to re-read values that barely change (camera clock drift, network
# info). 0 disables polling.
DEFAULT_INFO_POLL_INTERVAL = 3600

# Seconds the motion binary sensor stays "on" after a pushed motion event.
# The camera sends a single event per detection, never a "motion ended", so
# the off-edge has to be synthesized here.
MOTION_OFF_DELAY = 30
