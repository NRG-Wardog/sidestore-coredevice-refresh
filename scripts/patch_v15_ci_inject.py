#!/usr/bin/env python3
from pathlib import Path
import sys


def die(msg: str) -> None:
    raise SystemExit(msg)


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: patch_v15_ci_inject.py <v14_ci.sh> <output.sh>")
    src = Path(sys.argv[1]).read_text()

    # Run the v15 patch after every v14 source patch application.  The v14
    # runner calls apply_v14 both before native build and again after binary
    # injection; this gives us an automatic idempotence regression test.
    old = '''  python3 "$PATCH" \\
    "$idevice_root/idevice/src/remote_pairing/mod.rs" \\
    "$idevice_root/ffi/src/tunnel_provider.rs" \\
    "$gateway" \\
    "$refresh"\n'''
    new = old + '''  python3 "$BUILDER/scripts/patch_v15_tls_cdtunnel.py" \\
    "$idevice_root/idevice/src/remote_pairing/tunnel.rs" \\
    "$idevice_root/idevice/src/remote_pairing/tls_psk.rs"\n'''
    if src.count(old) != 1:
        die(f"apply_v14 injection anchor count={src.count(old)}")
    src = src.replace(old, new, 1)

    # Source barriers for the actual correction and diagnostics.
    old = '''  grep -q 'RSD_PASS' "$ffi"\n'''
    new = old + '''  grep -q '\\[SS-V15-TLS\\] TLS_PSK_START' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"\n  grep -q 'CdTunnel::handshake(tls_stream).await' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"\n  grep -q 'canonical-stream-read-exact' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"\n  ! sed -n '/pub async fn connect_tls_psk_tunnel_native/,/Wraps a `tokio::net::TcpStream` with TLS-PSK using OpenSSL/p' "$idevice_root/idevice/src/remote_pairing/tunnel.rs" | grep -q 'read_app_data().await'\n'''
    if src.count(old) != 1:
        die("v15 source barrier anchor missing")
    src = src.replace(old, new, 1)

    # Compile the patch script itself in every job.
    old = 'python3 -m py_compile "$PATCH"\n'
    new = old + 'python3 -m py_compile "$BUILDER/scripts/patch_v15_tls_cdtunnel.py"\n'
    if src.count(old) != 1:
        die("py_compile anchor missing")
    src = src.replace(old, new, 1)

    # Add binary markers to the post-build verification gate.
    old = '''  '[SS-V14-PROTOCOL] DYNAMIC_CONNECT_PASS' \\
'''
    new = old + '''  '[SS-V15-TLS] TLS_PSK_START' \\
  '[SS-V15-TLS] CDTUNNEL_HANDSHAKE_START' \\
  '[SS-V15-TLS] CDTUNNEL_HANDSHAKE_PASS' \\
'''
    if src.count(old) != 1:
        die("embedded marker anchor missing")
    src = src.replace(old, new, 1)

    # Keep existing paths stable but label evidence as v15.
    src = src.replace("SideStore v14 protocol matrix preflight", "SideStore v15 TLS/CDTunnel preflight")
    src = src.replace("SideStore v14 Protocol Matrix", "SideStore v15 TLS/CDTunnel")
    src = src.replace("echo 'preflight=PASS'", "echo 'v15_fix=canonical CDTunnel stream parser over TLS'\n    echo 'preflight=PASS'")
    src = src.replace("echo 'verification=PASS'", "echo 'v15_fix=canonical CDTunnel stream parser over TLS'\n  echo 'verification=PASS'")

    out = Path(sys.argv[2])
    out.write_text(src)
    out.chmod(0o755)
    print(f"v15 CI injection written to {out}")


if __name__ == "__main__":
    main()
