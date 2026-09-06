# SideStore CoreDevice Auto-Refresh v1.0.0

A modified SideStore build that refreshes SideStore and personally signed iOS apps directly on-device using the official App Store LocalDevVPN and Apple's CoreDevice stack.

## Download

Download the prebuilt IPA from the **Assets** section:

**`SideStore-CoreDevice-AutoRefresh-v1.0.0.ipa`**

SHA-256:

```text
CE675097AB87D93073C0FD807AD6C2C574452B8C099C831742B0C004D6DD1D21
```

You can also verify the download using **`SHA256SUMS.txt`** from the release assets.

## What this build provides

- On-device SideStore refresh path
- Free Apple Account / Personal Team support
- SideStore + up to two personally signed apps
- Official App Store LocalDevVPN
- Apple CoreDevice transport
- Native iOS `BGProcessingTask` scheduling
- Six-hour, daily, and weekly refresh schedules
- Preferred refresh time selection
- Persistent refresh history
- Siri refresh phrase support
- No jailbreak
- No paid Apple Developer account
- No custom VPN app
- No PC required during the intended refresh runtime

## Installation

Install the IPA **over your existing SideStore installation**.

**Do not delete your existing SideStore first.**

Replacing the existing installation helps preserve pairing data, Apple account state, the SideStore database, and existing app information.

After installation:

1. Open SideStore once.
2. Keep Developer Mode enabled.
3. Enable iOS Background App Refresh.
4. Keep the device connected to Wi-Fi.
5. Configure the official LocalDevVPN as described below.
6. Connect LocalDevVPN before opening the refresh path.
7. Open **Settings → Refreshing Apps → Refresh Schedule**.
8. Select Every Six Hours, Daily, or Weekly.

## Required LocalDevVPN route setup

For the normal refresh runtime, use only:

```text
iPhone Wi-Fi + official App Store LocalDevVPN
```

The important setting is **LocalDevVPN → Settings → Network Configuration**.

Configure **Tunnel IP** and **Device IP** as two unused IPv4 addresses inside the iPhone's current Wi-Fi subnet, using `/32` for both addresses, and enable **Allow Intermediate Addresses**.

Example for a `10.0.0.x/24` Wi-Fi network:

```text
Tunnel IP:  10.0.0.240/32
Device IP:  10.0.0.241/32
```

For a `192.168.1.x/24` Wi-Fi network, choose two unused `192.168.1.x/32` addresses instead.

The implementation does **not** hardcode `10.x` or `192.168.x`. It derives the LocalDevVPN peer from the active tunnel interface and routing table. If the iPhone moves to a different Wi-Fi subnet, reconfigure the LocalDevVPN addresses for that network.

Do not use the iPhone's actual Wi-Fi address, the router/gateway address, an address already used by another device, or an address outside the current Wi-Fi subnet.

## Verified

Verified on real iPhone hardware:

- LocalDevVPN → Lockdown/CoreDevice connection
- CoreDeviceProxy over TLS
- CDTunnel
- RSD
- AFC IPA staging
- InstallationProxy installation
- Post-install application verification
- Manual `Refresh All`
- SideStore self-refresh path
- Native background-task registration
- Native background-task scheduling
- Full unattended scheduled refresh with PC/USB disconnected

Proof device:

**iPhone 12 — iOS 26.6.1**

A verified manual `Refresh All` refreshed Spotify and SideStore over the CoreDevice transport in **18.571 seconds**.

The final PC-free scheduled proof uses the non-sensitive markers documented in [`docs/VERIFICATION.md`](VERIFICATION.md), including:

```text
[AUTO_REFRESH] TRIGGER source=bgprocessing
[AUTO_REFRESH] AUTH_PREFLIGHT_PASS
[AUTO_REFRESH] OPERATION_START
[AUTO_REFRESH] COMPLETE success=true
```

iOS still controls when `BGProcessingTask` actually executes. A selected refresh time is an earliest eligible execution time, not a guaranteed alarm.

## Compatibility testing

The current proof device is iPhone 12 / iOS 26.6.1. Additional successful and failed reports are welcome for other devices, iOS versions, and private Wi-Fi network ranges.

See [`docs/COMPATIBILITY.md`](COMPATIBILITY.md) and repository Issue #1.

## Build it yourself

The prebuilt IPA is produced from the same public build pipeline available in this repository.

1. Fork the repository.
2. Open **Actions**.
3. Run **Build Current SideStore**.
4. Download the generated IPA artifact.

The build uses pinned SideStore, minimuxer, idevice, and jktcp revisions documented in the repository.

## Security

Do not share or upload:

- pairing files
- Apple credentials
- certificate private keys
- personal signing certificates
- signed personal IPAs
- unnecessary device identifiers
- complete private device logs

The distributed IPA does not contain your Apple credentials or personal pairing data.

## Licensing

Original repository-authored builder scripts, tests, and documentation are MIT-licensed unless a file states otherwise. SideStore and other upstream projects retain their own applicable license terms, which continue to apply to upstream-derived code and the modified IPA. See `THIRD_PARTY_NOTICES.md` in the repository.

---

If this project is useful to you, consider starring the repository and reporting your device/iOS compatibility result.
