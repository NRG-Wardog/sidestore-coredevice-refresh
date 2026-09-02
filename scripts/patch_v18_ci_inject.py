#!/usr/bin/env python3
from pathlib import Path
import sys


def die(msg: str) -> None:
    raise SystemExit(msg)


def main() -> None:
    if len(sys.argv) != 3:
        die('usage: patch_v18_ci_inject.py <v17_ci.sh> <v18_ci.sh>')
    s = Path(sys.argv[1]).read_text()

    block = '''  python3 "$BUILDER/scripts/patch_v15_tls_cdtunnel.py" \\
    "$idevice_root/idevice/src/remote_pairing/tunnel.rs" \\
    "$idevice_root/idevice/src/remote_pairing/tls_psk.rs" \\
    "$idevice_root/ffi/src/tunnel_provider.rs"\n'''
    inject = block + '''  python3 "$BUILDER/scripts/patch_v18_full_pipeline.py" \\
    "$idevice_root/idevice/src/remote_pairing/tunnel.rs" \\
    "$idevice_root/idevice/src/remote_pairing/tls_psk.rs" \\
    "$idevice_root/idevice/src/services/rsd.rs"\n'''
    if s.count(block) != 1:
        die(f'v18 patch call anchor count={s.count(block)}')
    s = s.replace(block, inject, 1)

    anchor = '''  grep -q '\\[SS-V17-RSD\\] HANDSHAKE_PASS' "$ffi"\n'''
    extra = anchor + '''  grep -q 'REQUEST_WIRE_PARITY=pymobiledevice3-json-dumps' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"\n  grep -q '\\[SS-V18-TLS\\] RX_RECORD' "$idevice_root/idevice/src/remote_pairing/tls_psk.rs"\n  grep -q '\\[SS-V18-TLS\\] ALERT' "$idevice_root/idevice/src/remote_pairing/tls_psk.rs"\n  grep -q 'CBC_PADDING_BOUNDS_FAIL' "$idevice_root/idevice/src/remote_pairing/tls_psk.rs"\n  grep -q '\\[SS-V18-RSD\\] VALIDATION_PASS' "$idevice_root/idevice/src/services/rsd.rs"\n'''
    if s.count(anchor) != 1:
        die('v18 source barrier anchor missing')
    s = s.replace(anchor, extra, 1)

    py = 'python3 -m py_compile "$BUILDER/scripts/patch_v15_tls_cdtunnel.py"\n'
    if py not in s:
        die('v18 py_compile anchor missing')
    s = s.replace(py, py + 'python3 -m py_compile "$BUILDER/scripts/patch_v18_full_pipeline.py"\n', 1)

    marker_anchor = '''  '[SS-V17-CDT] HANDSHAKE_START' \\
'''
    marker_extra = marker_anchor + '''  '[SS-V18-CDT] REQUEST_WRITE_PASS' \\
  '[SS-V18-TLS] RX_RECORD' \\
  '[SS-V18-TLS] ALERT' \\
  '[SS-V18-TLS] APP_DECRYPT_PASS' \\
  '[SS-V18-RSD] VALIDATION_PASS' \\
'''
    if marker_anchor not in s:
        die('v18 binary marker anchor missing')
    s = s.replace(marker_anchor, marker_extra, 1)

    s = s.replace('SideStore v17 CDTunnel/RSD preflight', 'SideStore v18 full CDTunnel/RSD/signing preflight')
    s = s.replace('SideStore v17 CDTunnel/RSD Signing', 'SideStore v18 Full CDTunnel RSD Signing')

    out = Path(sys.argv[2])
    out.write_text(s)
    out.chmod(0o755)
    print(f'v18 CI runner written to {out}')


if __name__ == '__main__':
    main()
