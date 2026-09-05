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

The configurable Refresh Schedule UI is a new source change. Its date logic
and patch application have local tests; it has not yet been built or verified
on an iPhone. Existing device results above predate this UI.

For this feature, verify switching between six-hour and daily schedules,
changing the daily time, navigating back and reopening Settings, relaunching
SideStore, disabling refresh, and recovering from system scheduling errors.
Confirm the logged accepted date and the UI agree. Test the UI with large text
and both device orientations. Editing this schedule must leave any personal
Shortcuts automation unchanged.

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
