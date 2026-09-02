# SideStore V22 — RP-first same-device self-refresh

Pinned base:
- SideStore `3dc127ee6f17aa20f0863becd07c0c6043d8dddd`
- minimuxer `ef8a54ccbd08a0b679a556df20121c1e7e13be7e`
- IDevice `61c27041f8d3d0be4cc3e046ee04501649c9d66e`

Transport policy for iOS 26.4+ composite pairing files:
1. Keep LocalDevVPN peer `10.7.0.1`.
2. RemotePairing at `10.7.0.1:49152` is primary.
3. The dynamic listener returned by RemotePairing stays on the same peer IP; only the port changes.
4. TCP connect timeout is bounded at 15 seconds.
5. If RemotePairing fails, use classic Lockdown/CoreDevice fallback on `10.7.0.1:62078`.
6. The fallback uses the V13-proven native fixes: one-write Lockdown frames, `TCP_NODELAY`, and `QueryType` before `StartSession`.
7. After either tunnel succeeds, all modern services use RSD.

Not present: NAT44, ipsec/en0 destination rewriting, custom TLS, custom CDTunnel, or the V14 protocol matrix.

Runtime settings for the intended test:
- Wi-Fi: ON
- LocalDevVPN: ON (`10.7.1.1/32`, peer `10.7.0.1/32`)
- IKEv2: OFF
- WireGuard: OFF
- SideStore signed in to Apple account before refresh/install operations
