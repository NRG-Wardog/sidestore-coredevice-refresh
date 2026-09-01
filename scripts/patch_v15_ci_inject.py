#!/usr/bin/env python3
from pathlib import Path
import sys


def die(msg: str) -> None:
    raise SystemExit(msg)


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: patch_v15_ci_inject.py <v14_ci.sh> <output.sh>")
    src = Path(sys.argv[1]).read_text()

    old = '''  python3 "$PATCH" \\
    "$idevice_root/idevice/src/remote_pairing/mod.rs" \\
    "$idevice_root/ffi/src/tunnel_provider.rs" \\
    "$gateway" \\
    "$refresh"\n'''
    new = old + '''  python3 "$BUILDER/scripts/patch_v15_tls_cdtunnel.py" \\
    "$idevice_root/idevice/src/remote_pairing/tunnel.rs" \\
    "$idevice_root/idevice/src/remote_pairing/tls_psk.rs" \\
    "$idevice_root/ffi/src/tunnel_provider.rs"\n'''
    if src.count(old) != 1:
        die(f"apply_v14 injection anchor count={src.count(old)}")
    src = src.replace(old, new, 1)

    old = '''  grep -q 'RSD_PASS' "$ffi"\n'''
    new = old + '''  grep -q '\\[SS-V15-TLS\\] TLS_PSK_START' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"\n  grep -q '\\[SS-V17-CDT\\] HANDSHAKE_START' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"\n  grep -q 'tls_stream.write_app_data(&packet).await' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"\n  grep -q '\\[SS-V17-CDT\\] RESPONSE_HEADER_PASS' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"\n  grep -q 'prepend_read_data' "$idevice_root/idevice/src/remote_pairing/tls_psk.rs"\n  grep -q '\\[SS-V17-TLS\\] SERVER_FINISHED_FATAL' "$idevice_root/idevice/src/remote_pairing/tls_psk.rs"\n  grep -q '\\[SS-V17-RSD\\] CONNECT_START' "$ffi"\n  grep -q '\\[SS-V17-RSD\\] HANDSHAKE_PASS' "$ffi"\n  ! sed -n '/pub async fn connect_tls_psk_tunnel_native/,/Wraps a `tokio::net::TcpStream` with TLS-PSK using OpenSSL/p' "$idevice_root/idevice/src/remote_pairing/tunnel.rs" | grep -q 'CdTunnel::handshake(tls_stream).await'\n'''
    if src.count(old) != 1:
        die("v17 source barrier anchor missing")
    src = src.replace(old, new, 1)

    old = 'python3 -m py_compile "$PATCH"\n'
    new = old + 'python3 -m py_compile "$BUILDER/scripts/patch_v15_tls_cdtunnel.py"\n'
    if src.count(old) != 1:
        die("py_compile anchor missing")
    src = src.replace(old, new, 1)

    old = '''  '[SS-V14-PROTOCOL] DYNAMIC_CONNECT_PASS' \\
'''
    new = old + '''  '[SS-V15-TLS] TLS_PSK_START' \\
  '[SS-V17-CDT] HANDSHAKE_START' \\
  '[SS-V17-CDT] REQUEST_WRITE_PASS' \\
  '[SS-V17-CDT] RESPONSE_HEADER_PASS' \\
  '[SS-V17-CDT] HANDSHAKE_PASS' \\
  '[SS-V17-RSD] CONNECT_START' \\
  '[SS-V17-RSD] HANDSHAKE_PASS' \\
'''
    if src.count(old) != 1:
        die("embedded marker anchor missing")
    src = src.replace(old, new, 1)

    src = src.replace("SideStore v14 protocol matrix preflight", "SideStore v17 CDTunnel/RSD preflight")
    src = src.replace("SideStore v14 Protocol Matrix", "SideStore v17 CDTunnel/RSD Signing")
    src = src.replace(
        "echo 'preflight=PASS'",
        "echo 'v17_fix=single TLS-record CDTunnel request + fragmented response carry + RSD retry pipeline'\n    echo 'preflight=PASS'",
    )
    src = src.replace(
        "echo 'verification=PASS'",
        "echo 'v17_fix=single TLS-record CDTunnel request + fragmented response carry + RSD retry pipeline'\n  echo 'verification=PASS'",
    )

    out = Path(sys.argv[2])
    out.write_text(src)
    out.chmod(0o755)
    print(f"v17 CI injection written to {out}")


if __name__ == "__main__":
    main()
