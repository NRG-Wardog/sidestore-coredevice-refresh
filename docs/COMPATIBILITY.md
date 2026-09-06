# Device Compatibility

This page tracks real-device results for the SideStore Auto-Refresh CoreDevice path.

The implementation does not hardcode `10.x`, `192.168.x`, or another specific IPv4 range. It derives the LocalDevVPN peer from the active interface and routing table. The LocalDevVPN Tunnel IP and Device IP still need to be configured as unused `/32` addresses inside the iPhone's current Wi-Fi subnet.

## Verified proof device

| Device | iOS | LocalDevVPN | Manual Refresh All | Scheduled PC-free refresh |
| --- | --- | --- | --- | --- |
| iPhone 12 | 26.6.1 | Official App Store LocalDevVPN, same-subnet `/32` route | ✅ Verified | ✅ Final proof verified |

## Community compatibility reports

Additional reports are requested for:

- other iPhone and iPad models
- other iOS versions
- `10.x` private Wi-Fi networks
- `192.168.x` private Wi-Fi networks
- `172.16-31.x` private Wi-Fi networks
- unusual router/subnet configurations

Use [Issue #1](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/1) or the **Device verification** issue template to report a result.

A useful report includes:

- device model
- iOS version
- Wi-Fi network family (exact addresses are not required)
- whether official LocalDevVPN remained connected
- whether the same-subnet `/32` route was configured successfully
- manual refresh result
- scheduled PC-free result
- non-sensitive diagnostic markers when available

## Privacy

Do not publish pairing files, certificates, private keys, Apple credentials, signed personal IPAs, unnecessary device identifiers, exact private IP addresses if you do not want to disclose them, or complete private device logs.
