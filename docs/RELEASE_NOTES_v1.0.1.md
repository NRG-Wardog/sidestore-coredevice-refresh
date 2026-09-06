# SideStore CoreDevice Auto-Refresh v1.0.1

This release packages the current verified SideStore Auto-Refresh build for on-device refresh using the official App Store LocalDevVPN and Apple's CoreDevice stack.

## Release provenance

- Builder commit: `395d21310dd4a9d0ba71a46739ff5c1d47a862bf`
- GitHub Actions run: `34035803756`
- IPA size: `27,556,060` bytes
- IPA SHA-256: `EC0FE6109ABD92326BDA695AAFC95C2E19D6E8BD96E912F0030ACF46E72DEDD4`
- Verification: `PASS`

Pinned upstream revisions:

- SideStore: `394bb4eb331cb4afc23517af2fc847ec103af57f`
- minimuxer: `e0de126ec6773c02afe7d965def86ddffd79cbeb`
- idevice: `ebd7dadfc55d1c4facee3d11ecf5b28e20548b57`
- jktcp: `e674e1eee6d5943e13b1eba0bd24a9dd0b2fa020`

## What this build provides

- On-device SideStore refresh path through official LocalDevVPN + CoreDevice
- Free Apple Account / Personal Team support
- SideStore + up to two personally signed apps
- Manual `Refresh All` over CoreDevice
- Native iOS `BGProcessingTask` scheduling
- Six-hour, daily, and weekly refresh schedules
- Persistent refresh history with Manual / Scheduled source labels
- Preferred refresh time selection
- Siri refresh phrase support
- No jailbreak
- No paid Apple Developer account
- No custom VPN app
- No PC or USB required during the intended refresh runtime

## Final PC-free proof

A full unattended scheduled refresh with the PC disconnected has been verified on the current proof device.

Tested proof device:

- iPhone 12
- iOS 26.6.1
- official App Store LocalDevVPN
- same-subnet `/32` Tunnel IP + Device IP route configuration

The implementation dynamically discovers the LocalDevVPN peer route and is not hardcoded to `10.x` or `192.168.x` networks. The LocalDevVPN addresses must still be configured correctly for the iPhone's current Wi-Fi subnet.

## Installation

Install the IPA **over your existing SideStore installation**.

Do **not** delete SideStore first. Replacing the current installation preserves pairing data, account state, and the SideStore database used by the refresh path.

After installation:

1. Open SideStore once.
2. Configure the official LocalDevVPN for the current Wi-Fi subnet.
3. Keep LocalDevVPN connected.
4. Keep Developer Mode enabled.
5. Enable Background App Refresh in iOS.
6. Keep the iPhone on Wi-Fi.
7. Open **SideStore → Settings → Refreshing Apps → Refresh Schedule**.
8. Select Every Six Hours, Daily, or Weekly.

> iOS controls when `BGProcessingTask` actually runs. The configured time is an earliest eligible execution time, not a guaranteed alarm time.

## LocalDevVPN requirement

Use two unused IPv4 addresses in the iPhone's current Wi-Fi subnet and configure both as `/32` in **LocalDevVPN → Settings → Network Configuration**.

Do not use the iPhone's actual Wi-Fi IP, the router/gateway IP, an address already in use, or an address outside the current Wi-Fi subnet.

## Verification markers

Useful non-sensitive markers include:

```text
[AUTO_REFRESH] REGISTER_PASS
[AUTO_REFRESH] SCHEDULE_PASS
[AUTO_REFRESH] TRIGGER source=bgprocessing
[AUTO_REFRESH] AUTH_PREFLIGHT_PASS
[AUTO_REFRESH] OPERATION_START
[AUTO_REFRESH] COMPLETE success=true
```

## Security

Do not share pairing files, Apple credentials, certificate private keys, signed personal IPAs, unnecessary device identifiers, or complete private device logs.

## Compatibility reports

Additional iPhone/iPad, iOS, and Wi-Fi-network results are welcome. Successful and failed reports both help define the compatibility matrix.
