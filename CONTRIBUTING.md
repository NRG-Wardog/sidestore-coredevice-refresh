# Contributing

Thanks for helping improve the SideStore CoreDevice refresh path.

This repository contains build-time patches for pinned upstream SideStore dependencies. Contributions should preserve the current transport objective:

```text
official LocalDevVPN -> Lockdown -> CoreDeviceProxy -> CDTunnel -> RSD
```

## The most useful contributions right now

The highest-value contribution is **real-device compatibility testing** across additional iPhone/iPad models, iOS versions, and Wi-Fi network ranges.

The current proof device has completed manual CoreDevice refresh and a full PC-free scheduled refresh. Community testing should now focus on how broadly that result reproduces.

Useful reports include:

- iPhone/iPad model
- iOS version
- Wi-Fi IPv4 network family (`10.x`, `192.168.x`, `172.16-31.x`, or other; exact addresses are not required)
- whether official LocalDevVPN remained connected
- whether the same-subnet `/32` Tunnel IP and Device IP configuration worked
- whether manual `Refresh All` succeeded
- whether a native scheduled task triggered and completed with PC/USB disconnected
- non-sensitive `[AUTO_REFRESH]` and `[SIDESTORE_COREDEVICE]` markers
- whether SideStore and the selected apps received a new expiration date

See [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) and [Issue #1](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/1).

Do not upload a complete device log if it may contain private account, pairing, certificate, network, or device information. Reduce logs to the minimum non-sensitive evidence needed to reproduce or verify the result.

## Before opening a pull request

1. Run `python -m unittest discover -s tests -v`.
2. Run `git diff --check`.
3. Confirm patch scripts remain idempotent.
4. Confirm no pairing files, private keys, certificates, signed IPAs, unnecessary device identifiers, or personal logs are included.
5. Explain device, iOS, network, and upstream revision assumptions for transport changes.
6. For behavior changes, describe what was verified in CI and what was verified on a real device.

## Transport changes

Changes to the transport should include evidence from source inspection and a real device where practical.

Do not reintroduce QUIC or RemotePairing dynamic TCP as the production path without new primary evidence showing why the current Lockdown/CoreDevice path should change.

The current route discovery is intentionally range-agnostic: it derives candidate peers from the active tunnel interface and routing table rather than hardcoding `10.x` or `192.168.x`. Changes in this area should preserve that behavior.

## Background-refresh claims

Be precise about proof level:

- `REGISTER_PASS` proves registration.
- `SCHEDULE_PASS` proves iOS accepted the request.
- Neither marker alone proves that iOS executed the task.
- A real scheduled run is established by `TRIGGER`, operation markers, and a successful completion/result.

The current proof device has satisfied the full PC-free scheduled-refresh condition documented in [`docs/VERIFICATION.md`](docs/VERIFICATION.md). New compatibility claims should include equivalent non-sensitive evidence where practical.

## Security and privacy

Never commit or attach:

- pairing records
- certificate private keys
- Apple account credentials
- signed IPAs containing personal signing material
- unnecessary UDIDs/device identifiers
- exact private network addresses if they are not needed
- private device logs

If a bug report needs sensitive evidence, first reduce it to non-sensitive markers or a minimal redacted reproduction.

## Licensing

Original repository-authored builder scripts, tests, and documentation are covered by the MIT License unless a file says otherwise. Upstream projects and SideStore-derived binaries retain their applicable upstream licenses. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for the license scope and third-party notices.
