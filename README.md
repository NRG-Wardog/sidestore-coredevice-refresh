# SideStore Auto-Refresh — On-Device, No PC at Runtime

[![Build Current SideStore](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/build-current.yml/badge.svg)](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/build-current.yml)
[![Release](https://img.shields.io/github/v/release/NRG-Wardog/sidestore-auto-refresh)](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Keep **SideStore and up to two personally signed iOS apps refreshed directly from the iPhone** with a Free Apple Account, the official App Store **LocalDevVPN**, and Apple's CoreDevice stack.

The intended refresh runtime does **not** require a PC, USB connection, relay server, jailbreak, paid Apple Developer account, custom NetworkExtension, or custom VPN app.

> **Current proof status:** same-device CoreDevice transport, IPA staging/install, manual `Refresh All`, and native iOS background-task registration/scheduling are verified. The final proof milestone is a real iOS-scheduled refresh completing successfully while the PC is disconnected.

## Download

### Prebuilt IPA — recommended

**[Download SideStore CoreDevice Auto-Refresh v1.0.0](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.0/SideStore-CoreDevice-AutoRefresh-v1.0.0.ipa)**

Release page: **[v1.0.0](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.0)**

SHA-256:

```text
CE675097AB87D93073C0FD807AD6C2C574452B8C099C831742B0C004D6DD1D21
```

The release also includes [`SHA256SUMS.txt`](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.0/SHA256SUMS.txt) for integrity verification.

> Install the IPA **over your existing SideStore installation**. Do not delete SideStore first; replacing it preserves pairing data, account state, and the local database used by the refresh path.

Prefer to inspect and reproduce the build yourself? See [Build it yourself](#build-it-yourself).

## Quick start

1. Download the prebuilt IPA above.
2. Install it **over your existing SideStore** using your development installer.
3. Open SideStore once.
4. Configure **LocalDevVPN for your current Wi-Fi subnet** using the required setup below.
5. Keep the official **LocalDevVPN** connected.
6. Keep **Developer Mode** enabled.
7. Enable **Background App Refresh** in iOS.
8. Keep the device on **Wi-Fi**.
9. In SideStore, open **Settings → Refreshing Apps → Refresh Schedule**.
10. Choose Every Six Hours, Daily, or Weekly.

Weekly scheduling leaves little safety margin before free-account apps expire, and iOS may delay background execution. Six-hour or daily scheduling is safer.

> iOS decides when a `BGProcessingTask` actually runs. A selected time is an earliest eligible time, not a guaranteed alarm.

## Required LocalDevVPN setup

This step is **required** for the current CoreDevice transport path.

The default LocalDevVPN address range should not be left unchanged. Configure LocalDevVPN so its **Tunnel IP** and **Device IP** are two unused IPv4 addresses inside the **same subnet as the iPhone's current Wi-Fi network**, and use `/32` for both addresses.

For example, if the iPhone is connected to a Wi-Fi network in the form:

```text
192.168.1.x/24
```

choose two unused addresses from that same subnet, for example:

```text
Tunnel IP:  192.168.1.240/32
Device IP:  192.168.1.241/32
```

Do **not** copy those example addresses blindly. Use two addresses that are unused on your own Wi-Fi subnet.

In the official **LocalDevVPN** app:

1. Open **Settings → Network Configuration**.
2. Set **Tunnel IP** to the first unused address in your Wi-Fi subnet with `/32`.
3. Set **Device IP** to a second unused address in the same subnet with `/32`.
4. Enable **Allow Intermediate Addresses**.
5. Tap **Done**.
6. Tap **Save & Apply**.
7. Connect LocalDevVPN.
8. Open **Session Details** and verify that the tunnel is using the values you entered.

> If LocalDevVPN shows its original/default addresses again after the warning/confirmation screen, re-enter your custom Tunnel IP and Device IP, then use **Done → Save & Apply → Connect**.

### Important for local development and diagnostics

When running a local development or diagnostic build from a computer, the iPhone must be on the **same Wi-Fi network** as the development machine, and the VPN path must be brought up in the correct order before opening SideStore.

Use this order:

```text
1. Connect the iPhone to Wi-Fi.
2. Enable the required IKEv2/IPSec VPN profile if your local diagnostic setup uses it.
3. Connect the official LocalDevVPN.
4. Open SideStore.
```

For normal on-device refresh runtime, keep the official **LocalDevVPN** connected and keep the device on **Wi-Fi**. The computer, USB cable, and local diagnostic VPN are not part of the intended refresh runtime.

If SideStore cannot reach the local/CoreDevice path during development, do not start by changing the code. First verify the Wi-Fi subnet, the LocalDevVPN Tunnel IP / Device IP values, and the VPN connection order above.

### Important: which address SideStore uses

For CoreDevice/Lockdown traffic, the **Device IP is the peer address**. SideStore must reach the device peer through that address; the Tunnel IP is the local tunnel-side address.

In other words:

```text
Tunnel IP  -> LocalDevVPN tunnel side
Device IP  -> iPhone/CoreDevice peer used by SideStore
```

The current implementation discovers and validates this same-subnet `/32` peer route before opening the CoreDevice transport.

## Screenshots

### Refreshing Apps settings

![SideStore Refreshing Apps settings](docs/screenshots/settings-refreshing-apps.jpg)

### Refresh Schedule

![SideStore Refresh Schedule](docs/screenshots/refresh-schedule-main.jpg)

### Six-hour, daily, or weekly

![Six-hour, daily, and weekly refresh schedule options](docs/screenshots/refresh-schedule-options.jpg)

### Persistent refresh history

![SideStore refresh history](docs/screenshots/refresh-history.jpg)

These screens show the background-refresh controls, configurable scheduling, earliest eligible refresh time, notification status, and persisted manual refresh results.

## Why this exists

Apps signed with a Free Apple Account normally need to be refreshed within Apple's seven-day signing window. This project modifies SideStore so the refresh/install path can run over the iPhone's own LocalDevVPN/CoreDevice connection instead of depending on a computer during normal refresh operation.

### What you get

- **On-device SideStore refresh path** through official LocalDevVPN + CoreDevice.
- **Free Apple Account / Personal Team** support.
- **SideStore + up to two personally signed apps** within the normal free-account app limit.
- **Native iOS background scheduling** using `BGProcessingTask`.
- Configurable **six-hour, daily, or weekly** refresh scheduling.
- Persistent refresh history and diagnostic markers.
- No jailbreak and no custom VPN application.
- Reproducible GitHub Actions build from pinned upstream revisions.

## Verification status

| Capability | Status |
| --- | --- |
| LocalDevVPN → Lockdown/CoreDevice connection | ✅ Verified on real iPhone |
| CoreDeviceProxy TLS + CDTunnel + RSD | ✅ Verified |
| AFC IPA staging | ✅ Verified |
| InstallationProxy install | ✅ Verified |
| Post-install application verification | ✅ Verified |
| Manual `Refresh All` over CoreDevice | ✅ Verified |
| Native background-task registration | ✅ Verified |
| Native background-task scheduling | ✅ Verified |
| Schedule/history code and IPA build | ✅ CI verified |
| Full unattended scheduled refresh with PC disconnected | ⏳ Final device proof pending |

Tested transport hardware: **iPhone 12 running iOS 26.6.1**.

The latest verified manual `Refresh All` refreshed Spotify and SideStore successfully over the CoreDevice transport in **18.571 seconds**.

See [`docs/VERIFICATION.md`](docs/VERIFICATION.md) for the exact evidence, build hashes, device markers, and remaining proof requirements.

## Requirements

- Free Apple Account / Personal Team
- Valid Lockdown pairing file
- Developer Mode
- Wi-Fi
- Official unmodified LocalDevVPN from the App Store
- Same-subnet `/32` LocalDevVPN configuration

Tested topology:

```text
Tunnel IP:       TUNNEL_IP/32
Device peer IP:  DEVICE_PEER_IP/32
```

The PC and USB are needed for **building, initial installation, diagnostics, and log capture only**. They are not part of the intended refresh runtime.

## How it works

```text
official LocalDevVPN
        |
        v
Lockdown pairing
        |
        v
CoreDeviceProxy over TLS
        |
        v
CDTunnel
        |
        v
jktcp userspace IPv6 adapter
        |
        v
RSD
        |
        +--> AFC --> IPA staging
        |
        +--> InstallationProxy --> install/refresh
        |
        v
SideStore refresh pipeline
```

The implementation intentionally does **not** use QUIC or RemotePairing dynamic TCP in the current product path.

## What is patched

This repository does not vendor a permanent copy of SideStore or its dependency source trees. GitHub Actions checks out pinned upstream revisions and applies focused patches at build time.

### SideStore integration

- Composite pairing records prefer the Lockdown/CoreDevice path.
- LocalDevVPN route discovery derives and validates the device peer address.
- The obsolete IKEv2 requirement is not applied to the CoreDevice path.
- CoreDevice connections are created from the Lockdown pairing file and device endpoint.
- CoreDevice services use RSD for device-service discovery.
- AFC staging and InstallationProxy installation are available through RSD.
- The gateway reuses an active CoreDevice transport and detects stale heartbeat state before creating a new one.
- Pairing-file/provider cleanup is serialized to avoid ownership errors and double frees.
- Refresh/install operations emit public diagnostic markers without logging private keys or sensitive pairing material.

### CoreDevice / idevice transport

- CoreDeviceProxy keeps `com.apple.mobile.heartbeat` Marco/Polo alive for the full transport operation.
- Service TLS remains enabled when Lockdown returns `EnableServiceSSL=true`.
- CDTunnel requests are encoded as one contiguous frame before writing.
- Effective TCP MSS is limited to 1340 bytes because CoreDeviceProxy can drop larger host-to-device IPv6 packets despite advertising a larger MTU.
- AFC FFI operations use the local async runtime path to avoid a conflicting executor.
- AFC/provider and plist-array ownership is corrected at the FFI boundary.

### jktcp reliability

- Zero-window persist probes recover transfers when a window-update packet is lost.
- Window close/reopen events expose transport diagnostics.
- Timer-driven retransmission errors wake blocked readers.
- Transport write errors propagate instead of leaving a transfer waiting indefinitely.

## Native background refresh

SideStore registers a native iOS `BGProcessingTask` with:

```text
com.SideStore.SideStore.automatic-refresh
```

When iOS starts an enabled scheduled task, SideStore:

1. Reschedules the next task.
2. Waits for the CoreDevice transport boot sequence.
3. Starts the database if necessary.
4. Selects installed apps eligible for refresh.
5. Includes SideStore in the refresh operation.
6. Runs the existing signing, provisioning, staging, and installation pipeline.
7. Cancels the active operation if iOS expires the task.
8. Reports success only when all nested refresh results succeed.

Important markers:

```text
[AUTO_REFRESH] REGISTER_PASS
[AUTO_REFRESH] SCHEDULE_PASS
[AUTO_REFRESH] TRIGGER source=bgprocessing
[AUTO_REFRESH] ELIGIBLE_APPS
[AUTO_REFRESH] AUTH_PREFLIGHT_PASS
[AUTO_REFRESH] OPERATION_START
[AUTO_REFRESH] COMPLETE success=true
```

`REGISTER_PASS` and `SCHEDULE_PASS` prove registration/submission only. They do **not** prove that iOS executed the task.

Full PC-free background automation is considered proven only after a scheduled task runs with the PC disconnected and produces the trigger, operation, and successful completion evidence documented in [`docs/VERIFICATION.md`](docs/VERIFICATION.md).

## Refresh history and alerts

The patched build includes persisted refresh history for scheduled and manual operations. It records accepted schedules, starts, completion, failure, skipped runs, background expiration, and manual refresh results.

A local notification with sound is requested when iOS actually starts an enabled scheduled task. It is not pre-scheduled for the preferred refresh time; iOS remains in control of actual background execution.

History starts with installation of this feature and does not reconstruct old runs from previous logs.

## Build it yourself

The public GitHub Actions workflow reproduces the prebuilt IPA from pinned upstream revisions.

1. Fork this repository to your GitHub account.
2. Open **Actions** in your fork.
3. Enable Actions if GitHub asks.
4. Select **Build Current SideStore**.
5. Click **Run workflow** on `main`.
6. Open the successful run.
7. Download the `SideStore-v30-background-automation` artifact.
8. Extract the ZIP and use `SideStore.ipa`.

Workflow source: [`.github/workflows/build-current.yml`](.github/workflows/build-current.yml)

The workflow currently pins these revisions:

- SideStore: `394bb4eb331cb4afc23517af2fc847ec103af57f`
- minimuxer: `e0de126ec6773c02afe7d965def86ddffd79cbeb`
- idevice: `ebd7dadfc55d1c4facee3d11ecf5b28e20548b57`
- jktcp: `e674e1eee6d5943e13b1eba0bd24a9dd0b2fa020`

Patch scripts:

- `scripts/patch_coredevice_idevice.py`
- `scripts/patch_sidestore_integration.py`
- `scripts/patch_background_automation.py`
- `scripts/patch_local_idevice_package.py`
- `scripts/patch_jktcp_reliability.py`

The Action compiles the Rust and Swift components on macOS/Xcode, validates the patched source, runs repository and transport tests, verifies the IPA contents, and uploads the resulting IPA as a workflow artifact.

## Local repository checks

```bash
python -m unittest discover -s tests -v
```

Public-release safeguards are documented in:

- [`LICENSE`](LICENSE)
- [`CONTRIBUTING.md`](CONTRIBUTING.md)
- [`SECURITY.md`](SECURITY.md)
- [`docs/VERIFICATION.md`](docs/VERIFICATION.md)
- [`tests/test_repository.py`](tests/test_repository.py)

## Security

Never upload or commit:

- pairing files
- certificate private keys
- signed IPAs containing personal signing material
- unnecessary device identifiers
- private device logs

The public repository intentionally contains build-time patch scripts and documentation rather than personal signing material.

## Contributing

Bug reports, device verification results, transport diagnostics, documentation improvements, and focused fixes are welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request.

If you reproduce a successful **scheduled refresh with the PC disconnected**, include the non-sensitive markers and device/iOS version. That evidence directly advances the final verification milestone.

## Support the project

If this solves the SideStore seven-day refresh problem for you, **star the repository** so other SideStore users can find it.

If you test it on another iPhone/iOS version, open an issue with the result — successful or failed. Real-device compatibility data is more valuable than guesses.
