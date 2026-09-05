# SideStore CoreDevice self-refresh builder

This repository contains the current reproducible build chain for SideStore's
same-device LocalDevVPN -> CoreDeviceProxy -> CDTunnel -> RSD transport and
native background refresh scheduling.

Pinned revisions:

- SideStore: `394bb4eb331cb4afc23517af2fc847ec103af57f`
- minimuxer: `e0de126ec6773c02afe7d965def86ddffd79cbeb`
- idevice: `ebd7dadfc55d1c4facee3d11ecf5b28e20548b57`
- jktcp: `e674e1eee6d5943e13b1eba0bd24a9dd0b2fa020`

Use `.github/workflows/build-current.yml` to produce the current IPA. The
verified installable artifact is `SideStore-v30-background-automation.ipa`.

Never upload or commit a pairing file, certificate private key, or device
identifier.
