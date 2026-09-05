# SideStore CoreDevice Self-Refresh Builder

[![Build Current SideStore](https://github.com/NRG-Wardog/sidestore-coredevice-refresh/actions/workflows/build-current.yml/badge.svg)](https://github.com/NRG-Wardog/sidestore-coredevice-refresh/actions/workflows/build-current.yml)

This repository builds a modified SideStore that can refresh and install apps
over the same iPhone's official App Store LocalDevVPN connection. The final
runtime does not require a PC, USB, relay server, jailbreak, paid Apple
Developer account, custom NetworkExtension, or custom VPN application.

With a Free Apple Account / Personal Team, SideStore can keep itself and two
personally signed apps refreshed on the same iPhone through recurring refresh
automation, avoiding the normal seven-day interruption as long as the device
can run the scheduled refresh and the account remains valid.

The user still needs a Free Apple Account / Personal Team, a valid pairing
file, Developer Mode, Wi-Fi, and the unmodified LocalDevVPN App Store app.

## Current Transport

The current path is:

```text
LocalDevVPN / same-subnet /32 route
    -> Lockdown on the device peer at port 62078
    -> CoreDeviceProxy over TLS
    -> CDTunnel
    -> jktcp userspace IPv6 adapter
    -> RSD
    -> AFC and InstallationProxy
```

The tested LocalDevVPN topology uses same-subnet `/32` addresses:

```text
Tunnel IP:       TUNNEL_IP/32
Device peer IP:  DEVICE_PEER_IP/32
```

The implementation intentionally does not use QUIC or RemotePairing dynamic
TCP. Those paths are not used by the current product.

## Changes In SideStore

The build applies focused patches to upstream SideStore and its transport
dependencies. The source checkouts are downloaded at build time; they are not
stored permanently in this repository.

### SideStore integration

- Composite pairing records prefer the Lockdown/CoreDevice path.
- LocalDevVPN route discovery derives and validates the device peer address.
- The obsolete IKEv2 requirement is not applied to the CoreDevice path.
- CoreDevice connections are created from the Lockdown pairing file and device
  endpoint.
- CoreDevice services use RSD for device-service discovery.
- AFC staging and InstallationProxy installation are available through RSD.
- The gateway reuses an active CoreDevice transport and detects stale
  heartbeat state before creating a new one.
- Pairing-file and provider cleanup is serialized to avoid ownership errors and
  double frees.
- Refresh and installation operations emit public diagnostic markers without
  logging private keys or other sensitive pairing material.

### CoreDevice and idevice transport

- CoreDeviceProxy keeps a `com.apple.mobile.heartbeat` Marco/Polo session alive
  for the complete transport operation.
- Service TLS remains enabled after Lockdown returns `EnableServiceSSL=true`.
- CDTunnel requests are encoded as one contiguous frame before writing.
- The effective TCP MSS is limited to 1340 bytes because CoreDeviceProxy can
  drop larger host-to-device IPv6 packets despite advertising a larger MTU.
- AFC FFI operations use the local async runtime path so they do not create a
  conflicting executor for the userspace transport.
- AFC/provider and plist-array memory ownership is corrected at the FFI
  boundary.

### jktcp reliability

- Zero-window persist probes recover transfers when the peer's window closes
  and the window-update packet is lost.
- Window close and reopen events expose transport diagnostics.
- Timer-driven retransmission errors wake blocked readers.
- Transport write errors are propagated instead of silently leaving a transfer
  waiting forever.

## Automatic Refresh

SideStore now registers a native iOS `BGProcessingTask` with the identifier:

```text
com.SideStore.SideStore.automatic-refresh
```

Settings > Refreshing Apps > Refresh Schedule controls the native schedule.
Background Refresh can be enabled or disabled there. Choose Every Six Hours
(the existing default), or Daily and a preferred local time. Daily initially
shows 10:00. Changes are saved immediately and replace the pending request.
Disabling Background Refresh cancels that request.

The screen shows the earliest eligible date accepted by iOS, submission errors
with a retry action, and whether system Background App Refresh is unavailable.
The task requires network connectivity, but does not require external power.
iOS controls its actual execution time; the selected time is not a guaranteed
alarm. This control does not edit personal automations in Apple Shortcuts.
An existing daily Shortcuts automation must still be edited in Shortcuts.

Scheduling preserves a pending request across app launches and background
transitions so reopening SideStore does not postpone it. Daily calculations
use the local calendar, handle daylight-saving transitions, and recalculate
when a different time zone is observed.

When iOS runs the task, SideStore:

1. Reschedules the next task.
2. Waits for the CoreDevice transport boot sequence.
3. Starts the database if necessary.
4. Selects installed apps eligible for refresh.
5. Includes SideStore in the refresh operation.
6. Performs the existing signing, provisioning, staging, and installation
   pipeline.
7. Cancels the active operation if iOS expires the task.
8. Reports success only when all nested refresh results succeed.

Important log markers include:

```text
[AUTO_REFRESH] REGISTER_PASS
[AUTO_REFRESH] SCHEDULE_PASS
[AUTO_REFRESH] TRIGGER source=bgprocessing
[AUTO_REFRESH] ELIGIBLE_APPS
[AUTO_REFRESH] AUTH_PREFLIGHT_PASS
[AUTO_REFRESH] OPERATION_START
[AUTO_REFRESH] COMPLETE success=true
```

`REGISTER_PASS` and `SCHEDULE_PASS` prove that the task was registered and
accepted by iOS. They do not prove that iOS has executed it yet. A real
autonomous run requires the `TRIGGER`, operation, and successful completion
markers.

## Build

Run the GitHub Actions workflow:

```text
.github/workflows/build-current.yml
```

The workflow checks out these exact revisions:

- SideStore: `394bb4eb331cb4afc23517af2fc847ec103af57f`
- minimuxer: `e0de126ec6773c02afe7d965def86ddffd79cbeb`
- idevice: `ebd7dadfc55d1c4facee3d11ecf5b28e20548b57`
- jktcp: `e674e1eee6d5943e13b1eba0bd24a9dd0b2fa020`

The retained patch scripts are:

- `scripts/patch_coredevice_idevice.py`
- `scripts/patch_sidestore_integration.py`
- `scripts/patch_background_automation.py`
- `scripts/patch_local_idevice_package.py`
- `scripts/patch_jktcp_reliability.py`

The workflow builds on macOS with Xcode, compiles the Rust and Swift
components, verifies the IPA contents, and uploads the installable artifact.

## Installation And Runtime

Install the resulting IPA over the existing SideStore installation using the
development installer. Do not delete the existing SideStore first, because its
pairing data, account state, and local database must be preserved.

After installation:

- Open SideStore once.
- Keep official LocalDevVPN connected.
- Confirm iOS Background App Refresh is enabled.
- Confirm Developer Mode is enabled.
- Keep the phone on Wi-Fi.

The PC and USB are allowed for building, initial installation, diagnostics, and
log capture only. They are not part of the intended refresh runtime.

## Verification

The current IPA is:

```text
SideStore-v30-background-automation.ipa
```

The transport has been physically tested on an iPhone 12 running iOS 26.6.1.
The known-good path reached RSD, AFC, InstallationProxy, real IPA staging, and
post-install application browsing. The current V30 build additionally proved
background-task registration and scheduling on the device.

The remaining proof for full PC-free automation is a later iOS-scheduled run
showing the `TRIGGER` and `COMPLETE success=true` markers while the PC is
disconnected.

Never upload or commit a pairing file, certificate private key, or unnecessary
device identifier.

## Repository Quality

The repository contains only build-time patch scripts and documentation. It
does not vendor SideStore or its dependency source trees. The upstream source
is checked out at pinned revisions during the build and patched in the runner.

The public-release safeguards are documented in:

- `LICENSE`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `docs/VERIFICATION.md`
- `tests/test_repository.py`

Run the local checks with:

```text
python -m unittest discover -s tests -v
```

The current build architecture is:

```text
official LocalDevVPN
        |
        v
Lockdown pairing -> CoreDeviceProxy/TLS -> CDTunnel -> jktcp IPv6
                                                        |
                                                        v
                                             RSD -> AFC -> InstallationProxy
                                                        |
                                                        v
                                             SideStore refresh/install
```
