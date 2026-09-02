# V21 — Lockdown-first diagnosis and final build gate

## Decision

V20 correctly detected the active IKEv2 interface. The transport assumption was
wrong: `ipsec7` is another local address of the same iPhone, not a remote transit
hop. Rewriting an inner LocalDevVPN packet toward that address produced no
SYN-ACK and no reverse NAT packet.

V21 removes the active RPPairing/NAT experiment. It uses the complete classic
Lockdown record already present in the imported iLoader pairing file.

## Evidence from the 2026-09-02 08:16 device run

The installed V20 binary selected the IKEv2 address:

```text
[EMP-TRANSIT] selected interface=ipsec7 ipv4=10.31.0.146 priority=ikev2
TX-NAT44-FWD ... -> 10.31.0.146:<dynamic-port>
```

No `TX-NAT44-REV` followed, and the connection ended in
`DYNAMIC_CONNECT_FAIL`. Direct `en0` candidates still completed TLS-PSK and then
received `ALERT level=1 description=0` after the first CDTunnel request.

The pairing file itself parsed in both formats:

```text
RP_PAIRING_PARSE_SUCCESS
LOCKDOWN_PAIRING_PARSE_SUCCESS
HYBRID_FILE has_rp=true has_lockdown=true
```

V20 nevertheless selected:

```text
backend=idevice
mode=rppairing
TRANSPORT_SELECT path=v14-rp-protocol-matrix
```

That selection defect is the boundary fixed by V21.

## V21 transport

```text
complete composite pairing file
    -> classify as Lockdown
    -> LibimobiledeviceGateway
    -> fake usbmuxd on 127.0.0.1:27015
    -> network device advertised at LocalDevVPN peer 10.7.0.1
    -> released libimobiledevice XCFramework
    -> lockdownd:62078
    -> StartService / app services
```

IKEv2 remains a runtime prerequisite for Lockdown mode on iOS 26.4+, but its
local address is never used as a synthetic NAT destination.

## Code changes

- Complete Lockdown keys are evaluated before RemotePairing keys.
- `Minimuxer.shared()` is pinned to `LibimobiledeviceGateway`.
- The cached-backend comparison uses the previous backend rather than the value
  assigned immediately before the comparison.
- RemotePairing-only identity fields are removed from the pair record returned
  by fake usbmuxd; classic Lockdown material remains.
- A UDID is accepted only after a live Lockdown `GetValue` succeeds.
- The explicit LocalDevVPN peer is retained while reachability converges.
- Fake-usbmuxd, device lookup, Lockdown handshake, and GetValue stages emit
  secret-free markers.
- V19/V20 NAT44 and the V14 protocol matrix are not applied to the product.
- Released IDevice, libimobiledevice, and EMProxy XCFrameworks are consumed; no
  Rust native library is rebuilt.

## Runtime acceptance sequence

A successful device run must contain, in order:

```text
[SS-V21-LOCKDOWN] backend=libimobiledevice
[SS-V21-LOCKDOWN] pairing selected=lockdown composite=true
[SS-V21-LOCKDOWN] usbmuxd socket retargeted
[SS-V21-LOCKDOWN] fake usbmuxd listening
[SS-V21-LOCKDOWN] usbmux ListDevices peer configured
[SS-V21-LOCKDOWN] usbmux ReadPairRecord
[SS-V21-LOCKDOWN] device lookup pass
[SS-V21-LOCKDOWN] lockdownd handshake pass
[SS-V21-LOCKDOWN] GetValue pass key=UniqueDeviceID
[SS-V21-LOCKDOWN] UniqueDeviceID query pass
```

The active product path must not contain:

```text
EMP-NAT44
EMP-TRANSIT
MATRIX_START
SS-V14-CREATE-LISTENER
```

## Action-credit policy

The only V21 workflow is `SideStore v21 Final Lockdown Pipeline`.

It is triggered explicitly by either:

- a manual dispatch with the exact confirmation `BUILD_V21`; or
- a commit that changes only `.github/v21-build-trigger` on
  `v21-lockdown-first`.

The workflow first runs a short Ubuntu gate. macOS starts only after that gate
passes. The macOS job performs no IDevice or EMProxy Rust compilation, skips the
dSYM archive, uses released native XCFrameworks, avoids IPA recompression, and
uploads diagnostics only on failure.
