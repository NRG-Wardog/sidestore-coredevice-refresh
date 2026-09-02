# SideStore v19 runtime diagnosis

## Decision

The v18 failure is not a pairing-file, WireGuard, PairVerify, listener-creation,
TLS-key, CBC, JSON-spacing, or CDTunnel framing failure.

The failing boundary is the same-device connection to the dynamic RemotePairing
listener. The listener is reachable only through the phone's physical `en0`
address. That direct self-connect completes TLS-PSK, accepts the first CDTunnel
record, then `remotepairingd` sends an authenticated TLS 1.2 warning
`close_notify` (`level=1`, `description=0`) without a CDTunnel response.

## Evidence from the 2026-09-02 device trace

1. The imported file parses successfully as both RemotePairing and Lockdown.
2. The LocalVPN/WireGuard route becomes reachable.
3. RemotePairing control on `10.7.0.1:49152` completes PairVerify.
4. `createListener` succeeds and returns a high dynamic port.
5. A connection to that port through an `en0` IPv4/IPv6 address succeeds.
6. TLS-PSK selects `Aes256CbcSha384`; server Finished verifies.
7. The complete 58-byte pymobiledevice3-compatible CDTunnel request is written.
8. The next authenticated server record is TLS alert `warning/close_notify`.
9. The same result repeats on physical IPv4 and IPv6 candidates.
10. The Lockdown fallback connects to `62078`, ACKs the combined QueryType frame,
    then the local peer resets/closes it. It is not a valid fallback for this
    same-device path.

Because the alert decrypts with a valid server MAC after a verified Finished,
the TLS record state and PSK are correct. The peer is intentionally terminating
this connection at the application/transport-identity boundary.

## Fix

`apply_emproxy_nat44_dynamic.py` adds stateful NAT44 only for initiating TCP SYNs
to high dynamic ports on the virtual peer address:

```text
Client-visible tuple:
10.7.0.10:client -> 10.7.0.1:listener

Listener-visible tuple:
10.7.0.1:client -> en0-ipv4:listener
```

Replies are translated back to the original client-visible tuple. IPv4 and TCP
checksums are recalculated. Fixed RemotePairing and Lockdown ports (`49152` and
`62078`) remain on the proven stock reflection path.

This removes the physical self-connect while using the existing IPv4 VPN route;
it does not require an additional IPv6 address or WireGuard AllowedIPs change.

## Runtime acceptance gates

A successful on-device run must show this sequence:

```text
[EMP-NAT44] dynamic listener bridge active
[EMP-NAT44] selected physical IPv4=...
TX-NAT44-FWD
TX-NAT44-REV
[SS-V15-TLS] TLS_PSK_PASS
[SS-V18-CDT] REQUEST_WRITE_PASS
[SS-V18-CDT] RESPONSE_HEADER_PASS
[SS-V18-CDT] HANDSHAKE_PASS
[SS-V17-RSD] HANDSHAKE_PASS
[SS-V18-RSD] VALIDATION_PASS
[SS-V14-READY] TRANSPORT_PASS
```

If `TX-NAT44-FWD` appears without `TX-NAT44-REV`, the remaining issue is iOS
routing/local delivery to `en0`. If both appear but the peer still sends
`close_notify`, the NAT tuple is working and the next investigation is the
listener's process/peer metadata rather than TLS or CDTunnel bytes.

## Low-credit CI policy

The v19 workflow is inert until `.github/v19-build-trigger` changes. It runs a
small Linux preflight against only the changed EMProxy crate, then exactly one
macOS build. It removes the unused standalone `bindgen-cli` installation, does
not upload success diagnostics twice, applies strict timeouts, and cancels an
older in-progress run if a replacement is explicitly triggered.
