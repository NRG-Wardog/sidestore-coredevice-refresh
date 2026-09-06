# Verification Report

## Scope

The current build targets an iPhone 12 running iOS 26.6.1 with a Free Apple
Account, Developer Mode, official App Store LocalDevVPN, Wi-Fi, and a valid
Lockdown pairing file.

The PC is used only for building, initial installation, diagnostics, and log
capture. It is not part of the intended refresh runtime.

## Proven

- Lockdown reaches CoreDeviceProxy on the LocalDevVPN same-subnet route.
- CoreDeviceProxy service TLS is enabled.
- CDTunnel reaches RSD through the userspace IPv6 adapter.
- Heartbeat Marco/Polo traffic remains active during transport operations.
- AFC stages a real signed IPA through RSD.
- InstallationProxy installs the staged IPA.
- Post-install browsing confirms the installed application.
- SideStore registers and submits the native background processing task.
- A manual `Refresh All` run refreshed Spotify and SideStore successfully over
  the CoreDevice transport in 18.571 seconds.
- Full unattended scheduled refresh with the PC disconnected is verified on the
  current proof device.

## Final PC-free proof

The final proof condition is a scheduled iOS background task running while the
PC and USB are disconnected, then reaching the refresh operation and completing
successfully.

Required non-sensitive proof markers:

```text
[AUTO_REFRESH] TRIGGER source=bgprocessing
[AUTO_REFRESH] AUTH_PREFLIGHT_PASS
[AUTO_REFRESH] OPERATION_START
[AUTO_REFRESH] COMPLETE success=true
```

For external reports, include only non-sensitive evidence:

- iPhone model
- iOS version
- LocalDevVPN remained connected
- PC/USB was disconnected during the scheduled run
- refreshed app names if they are safe to disclose
- screenshot of the refresh history screen after completion
- the public diagnostic markers above

Do not upload pairing files, certificates, private keys, full private device
logs, unnecessary device identifiers, or signed IPAs containing personal signing
material.

## Current release IPA

The public v1.0.0 IPA is 27,555,473 bytes with SHA-256:

```text
ce675097ab87d93073c0fd807ad6c2c574452b8c099c831742b0c004d6dd1d21
```

The local `new.ipa` used for verification matches this hash.

## Refresh history build

Builder commit `12c9ffb6f906000f8ad87ce820e64600f71ffc97` passed
[Actions run 33984781809](https://github.com/NRG-Wardog/sidestore-coredevice-refresh/actions/runs/33984781809).
It adds persisted history with **Manual** and **Scheduled** source labels,
manual refresh results, and a local start-alert request for native scheduled
tasks. All eight local tests passed before dispatch. The macOS repository
checks, transport tests, full iOS build, and IPA verification passed in CI.

Tests cover old history decoding, partial failures, missing results, terminal
event deduplication, and the generated manual refresh entry point with mocked
pipeline results. Background callers opt out of manual recording to avoid
duplicate entries. Existing refresh callbacks remain intact.

Local checks confirmed archive integrity and the expected history, start-alert,
and manual-result strings in the executable.

## Schedule UI builds

The configurable Refresh Schedule UI was built successfully from builder
commit `63f1c0d148c999ae7e93546582c3f65968223642` in
[Actions run 33972557213](https://github.com/NRG-Wardog/sidestore-coredevice-refresh/actions/runs/33972557213).
All seven local tests passed, including generated Swift date and scheduler
tests and patch idempotence against the pinned source. The macOS runner also
passed a SwiftUI type check, transport tests, the full iOS build, and IPA
verification.

The downloaded IPA is 27,524,692 bytes with SHA-256:

```text
fb2c3f710a8151296ab82270b8d838c31d98b05ada3df4ac1158f038793b4ec5
```

Its executable contains the schedule screen and preference keys.

Weekly scheduling was added in builder commit
`7e2384709f7beebd87890b6beaf5497dd2310c63` and built successfully in
[Actions run 33980149032](https://github.com/NRG-Wardog/sidestore-coredevice-refresh/actions/runs/33980149032).
The same local and macOS checks passed, with additional cases for weekly
rollover, weekday changes, legacy daily preferences, and daylight-saving
transitions. The verified IPA is 27,530,865 bytes with SHA-256:

```text
cb5691bd04fd18bbf691ab6a8296ab23f1ba2cb7dc3a20a5c710474ecd03d2a3
```

Its executable contains the frequency and weekday preference keys.

## Additional device coverage still useful

The current proof device is verified. Additional community reports are still
valuable for compatibility coverage across:

- other iPhone models
- other iOS versions
- different Wi-Fi subnets, including `10.x`, `172.16.x`-`172.31.x`, and
  `192.168.x`
- six-hour, daily, and weekly schedules
- large text / accessibility settings
- repeated refresh cycles over multiple days
