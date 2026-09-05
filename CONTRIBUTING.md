# Contributing

This repository contains build-time patches for pinned upstream SideStore
dependencies. Contributions should preserve the current transport objective:

```text
official LocalDevVPN -> Lockdown -> CoreDeviceProxy -> CDTunnel -> RSD
```

Before opening a pull request:

1. Run `python -m unittest discover -s tests -v`.
2. Run `git diff --check`.
3. Confirm the patch scripts are idempotent.
4. Confirm no pairing files, private keys, device identifiers, signed IPAs, or
   personal logs are included.
5. Explain device, iOS, and upstream revision assumptions for transport changes.

Changes to the transport must include evidence from source inspection and a
real device where practical. Do not reintroduce QUIC or RemotePairing dynamic
TCP as the production path without new primary evidence.

The upstream projects retain their own licenses. This repository's patch
scripts and documentation are licensed under the MIT License in `LICENSE`.
