# V23 RemotePairing dynamic-listener diagnostic

## Runtime question
V22 proved that `10.7.0.1:49152` reaches RemotePairing but the follow-up connection to the dynamic listener times out before TLS-PSK starts. V23 keeps the canonical protocol and destination unchanged and adds enough telemetry to identify the exact failure.

## Native markers

- `RP_CONTROL_CONNECT_START` — target, route-selected local source, paired state.
- `RP_CONTROL_CONNECT_PASS` — TCP local/peer endpoints and timing for port 49152.
- `RP_PAIRING_START/PASS/FAIL` — pair-verify/setup envelope only; no key material is logged. `psk_len` is length only.
- `RP_CREATE_LISTENER_START/PASS/FAIL` — proves whether remoted accepted createListener and exposes only the returned dynamic port.
- `RP_DYNAMIC_ROUTE` — UDP route lookup for `10.7.0.1:<dynamic>`; this does not send application data and identifies the source address selected by the kernel.
- `RP_DYNAMIC_CONNECT_START/PASS/FAIL` — exact dynamic target, TCP local/peer endpoints when successful, route source before/after and elapsed time on failure.
- `RP_TLS_PSK_START/PASS/FAIL` — begins only after dynamic TCP connect succeeds.
- `RP_TUNNEL_INFO` — tunnel client/server addresses, MTU and RSD port after TLS/CDTunnel succeeds.
- `RP_RSD_CONNECT_*` and `RP_RSD_HANDSHAKE_*` — final RSD stages.
- `RP_TOTAL_PASS/FAIL` — total elapsed time for a single serialized RP attempt.

## Swift-side controls

- `TUNNEL_LOCK_WAIT/ACQUIRED/RELEASE` serializes tunnel creation. V22 logs showed two `ensureRPConnection()` calls starting about 120 ms apart and sharing the same mutable RP pairing handle.
- Native IDevice error-level logging is forced on for V23 so `tracing::error!` stage markers are visible even when normal SideStore logging is disabled.
- `NIL_UDID_TRANSPORT_FAIL` replaces the misleading boot message that previously reported `UDID: nil` as a successful connection test.

## Architecture barriers

V23 does not add NAT44, ipsec-first routing, en0 rewriting, a custom TLS implementation, a custom CDTunnel implementation, or a protocol matrix. The RemotePairing dynamic destination remains the address returned by the stock SideStore/IDevice algorithm: same control IP, returned dynamic port.

## Phone configuration for the diagnostic run

- Wi-Fi: ON
- LocalDevVPN: ON
- LocalDevVPN interface: `10.7.1.1/32`
- LocalDevVPN peer: `10.7.0.1/32`
- IKEv2: OFF
- WireGuard: OFF
- Other VPNs: OFF
