# SideStore CoreDevice Auto-Refresh v1.0.2

This release delivers the verified SideStore build with same-device CoreDevice refresh and bounded on-device diagnostic logging. It is designed for a Free Apple Account / Personal Team, the official App Store LocalDevVPN, Wi-Fi, a valid pairing file, and Developer Mode.

## Release provenance

- Builder commit: `f33487d473e09620493d2a8d82e8e37c9bdef32b`
- GitHub Actions run: `34045788967`
- IPA size: `27,566,058` bytes
- IPA SHA-256: `120ba06c51d4d235743451b065968dc94f7c7374cacb955827860254e01b5a76`
- Verification: `PASS`

## What's included

- Same-device refresh through official LocalDevVPN and Apple's CoreDevice stack
- Free Apple Account / Personal Team support
- Manual, six-hour, daily, and weekly refresh options
- Preferred local refresh time selection
- Persistent refresh history with Manual and Scheduled labels
- Clear background-task start, success, skipped, and failure diagnostics
- Console-log retention capped at five files and 2 MiB per active log
- Siri refresh shortcut support
- No jailbreak, paid developer account, custom VPN app, relay, or PC during refresh runtime

## Installation

Install the IPA over the existing SideStore installation. Do not delete SideStore first; replacing it preserves pairing data, account state, and the SideStore database.

After installation:

1. Open SideStore once.
2. Keep Developer Mode and Background App Refresh enabled.
3. Connect the official LocalDevVPN and keep the iPhone on Wi-Fi.
4. Open **Settings -> Refreshing Apps -> Refresh Schedule**.
5. Select the required schedule and verify the refresh history after the next run.

iOS controls when a background task runs. The selected time is an earliest eligible time, not a guaranteed alarm time.

## Compatibility

Verified on an iPhone 12 running iOS 26.6.1 with the official App Store LocalDevVPN and a same-subnet `/32` tunnel/device configuration.

Do not share pairing files, Apple credentials, certificate private keys, signed personal IPAs, or complete private device logs.
