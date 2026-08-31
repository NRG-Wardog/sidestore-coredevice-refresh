# v11 clean RP transport design

This branch replaces the cumulative v1-v10 experimental transport stack with the upstream minimuxer RemotePairingKit/libimobiledevice path.

Principles:

1. Do not bootstrap a Lockdown PairRecord through 10.7.0.1:62078.
2. Do not patch dynamic listeners with NAT46, physical-interface self-connect, or source binding.
3. Use the exact pinned SideStore/minimuxer sources and their stock EMProxy/RPPairing binaries.
4. Route RP pairing files to LibimobiledeviceGateway; do not fall back to the known-broken Idevice dynamic path.
5. Add stage diagnostics and fail closed at the first transport error.
6. Keep the build workflow manual-only until all local structural and patch tests pass.

The manual workflow must not be enabled or invoked until `tests/run_v11_local_tests.py` succeeds against the exact pinned sources.
