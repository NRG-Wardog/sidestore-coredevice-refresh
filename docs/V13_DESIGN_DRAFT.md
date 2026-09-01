# SideStore v13 Lockdown protocol correction

This branch is created from v12 after the on-device log proved:

- the merged pairing file parses as both RemotePairing and Lockdown;
- TCP 62078 completes SYN/SYN-ACK;
- lockdownd acknowledges only the first 4-byte plist length prefix and then resets;
- the exact-endpoint RemotePairing dynamic port receives SYNs but no SYN/ACK;
- multiple FFI operations overlap on the default global concurrent queue.

v13 corrects the first Lockdown exchange rather than changing pairing or VPN addressing:

1. coalesce each plist length prefix and payload into one write;
2. perform QueryType before GetValue/StartSession, matching canonical lockdownd clients;
3. expose stage-specific, secret-free native diagnostics;
4. serialize IdeviceGateway FFI work;
5. retain v12 hybrid parsing and exact-endpoint RP fallback;
6. build and inject a pinned, patched IDevice XCFramework;
7. add bounded EMProxy payload-length telemetry for 62078 and dynamic listener ports.

The build must fail before macOS compilation if structural, idempotence, ownership,
framing, serialization, or privacy checks fail.
