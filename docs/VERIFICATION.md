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

## Current device markers

```text
[AUTO_REFRESH] REGISTER_PASS
[AUTO_REFRESH] SCHEDULE_PASS
```

These markers prove task registration and submission only. iOS controls the
actual execution time of a background task.

## Remaining proof

Full PC-free automation is proven only after a scheduled task runs while the
PC is disconnected and produces:

```text
[AUTO_REFRESH] TRIGGER source=bgprocessing
[AUTO_REFRESH] AUTH_PREFLIGHT_PASS
[AUTO_REFRESH] OPERATION_START
[AUTO_REFRESH] COMPLETE success=true
```

Do not describe the scheduled refresh as fully proven until those markers and
the resulting refresh are observed.
