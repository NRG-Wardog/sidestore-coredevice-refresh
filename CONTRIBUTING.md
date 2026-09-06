# Contributing

Thanks for helping improve the SideStore CoreDevice refresh path.

This repository contains build-time patches for pinned upstream SideStore dependencies. Contributions should preserve the current transport objective:

```text
official LocalDevVPN -> Lockdown -> CoreDeviceProxy -> CDTunnel -> RSD
```

## The most useful contributions right now

The highest-value contribution is **real-device verification**, especially a native scheduled refresh completing while the PC is disconnected.

Useful reports include:

- iPhone/iPad model
- iOS version
- whether LocalDevVPN remained connected
- whether manual `Refresh All` succeeded
- whether a native scheduled task actually triggered
- non-sensitive `[AUTO_REFRESH]` and `[SIDESTORE_COREDEVICE]` markers
- whether SideStore and the selected apps received a new expiration date

Do not upload a complete device log if it may contain private account, pairing, certificate, or device information. Reduce logs to the minimum non-sensitive evidence needed to reproduce or verify the result.

## Before opening a pull request

1. Run `python -m unittest discover -s tests -v`.
2. Run `git diff --check`.
3. Confirm patch scripts remain idempotent.
4. Confirm no pairing files, private keys, certificates, signed IPAs, unnecessary device identifiers, or personal logs are included.
5. Explain device, iOS, and upstream revision assumptions for transport changes.
6. For behavior changes, describe what was verified in CI and what was verified on a real device.

## Transport changes

Changes to the transport should include evidence from source inspection and a real device where practical.

Do not reintroduce QUIC or RemotePairing dynamic TCP as the production path without new primary evidence showing why the current Lockdown/CoreDevice path should change.

## Background-refresh claims

Be precise about proof level:

- `REGISTER_PASS` proves registration.
- `SCHEDULE_PASS` proves iOS accepted the request.
- Neither marker proves that iOS executed the task.
- A real scheduled run requires `TRIGGER`, operation markers, and a successful completion/result.

Do not describe PC-free scheduled refresh as fully proven until the criteria in [`docs/VERIFICATION.md`](docs/VERIFICATION.md) are satisfied.

## Security and privacy

Never commit or attach:

- pairing records
- certificate private keys
- Apple account credentials
- signed IPAs containing personal signing material
- unnecessary UDIDs/device identifiers
- private device logs

If a bug report needs sensitive evidence, first reduce it to non-sensitive markers or a minimal redacted reproduction.

## Licensing

The upstream projects retain their own licenses. This repository's patch scripts and documentation are licensed under the MIT License in [`LICENSE`](LICENSE).
