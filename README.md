# PPRPC Camera — Home Assistant integration

Fully **local** Home Assistant integration for cheap **XC Things PPRPC** Wi‑Fi
cameras (FT04 / Beken BK7252 — the ones set up with the vendor apps
`FtyCam` / `Fchan`). No cloud, no account, no credentials: the camera is
discovered by UDP broadcast and authenticated with a single‑use token from the
discovery reply.

Built on the [`aiopprpc`](https://github.com/DevLn/aiopprpc) library, which
implements the reverse‑engineered PPRPC protocol (see
[FT04_COMMAND_REFERENCE.md](https://github.com/DevLn/pprpc_workspace/blob/main/docs/FT04_COMMAND_REFERENCE.md)
in the workspace repo).

> ⚠️ Only **one client** may be connected to the camera at a time. Close the
> vendor app while Home Assistant is using the camera.

## Installation

**HACS (custom repository):** add this repository as a custom repository of
type *Integration*, then install *PPRPC Camera* and restart Home Assistant.

**Manual:** copy `custom_components/pprpc_camera/` into your Home Assistant
`config/custom_components/` folder and restart.

Then add the integration: *Settings → Devices & services → Add integration →
PPRPC Camera*, and enter the camera's IP address. Cameras on the local network
are also discovered automatically (UDP broadcast, every 10 minutes by
default).

## Entities

| Entity | Type | Notes |
|---|---|---|
| Camera | `camera` | MJPEG 640×480 ~10 fps; `turn_on` holds the stream open |
| Flashlight | `button` | The white light is a **stateless toggle** on the wire (any command flips it, and even a read actuates it), so there is no state to show — one press flips it |
| Reboot | `button` | |
| Sync time | `button` | Sets the camera clock to Home Assistant's time |
| Status LED | `switch` | Write-only on the wire; assumed state |
| Orientation | `select` | Upright / mirror / upside down / 180° |
| Night vision | `select` | IR‑cut: on / off / auto |
| Night light mode | `select` | Intelligent / B&W / full color (labels unverified — see docs) |
| Motion sensitivity | `select` | Off / low / medium / high |
| Signal strength, Wi‑Fi network | `sensor` | Diagnostic |
| IP, MAC, clock drift, SDK | `sensor` | Diagnostic, disabled by default |
| Motion | `binary_sensor` | **Disabled by default**: motion push has only been observed on the vendor's cloud channel; LAN‑direct push is unverified |

## Services

All target the camera entity:

- **`pprpc_camera.ptz`** — pan (`LEFT`/`RIGHT`) and/or tilt (`UP`/`DOWN`) one
  step.
- **`pprpc_camera.reboot`** — reboot the camera.
- **`pprpc_camera.talk`** — play any audio media / TTS through the camera
  speaker (transcoded by ffmpeg to the camera's 8 kHz PCM).

## Configuration

Everything works with zero YAML. Optional globals (each also settable per
camera in the integration's options, which take precedence):

```yaml
pprpc_camera:
  discovery:
    enabled: true
    duration: 10      # seconds to listen during each discovery pass
    interval: 600     # seconds between passes
    ip: 192.168.1.255 # optional: limit discovery to specific broadcast IP(s)
  idle_disconnect_delay: 5   # seconds to keep the session warm after the last command
  status_poll_interval: 300  # Wi-Fi signal refresh (0 = never)
  info_poll_interval: 3600   # clock/network refresh (0 = never)
```

## How it connects

The camera drops idle TCP connections after ~3 s, and its auth token expires
per discovery, so the integration connects **on demand**: each command or
stream opens (or reuses) a session, and the session is torn down a few seconds
after the last user releases it. Polling only happens while an entity that
needs the data is enabled.
