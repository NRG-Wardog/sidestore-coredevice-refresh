#!/usr/bin/env python3
from pathlib import Path
import sys


def die(msg: str) -> None:
    raise SystemExit(msg)


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        die(f"{label} anchor count={count}")
    return source.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 3:
        die('usage: patch_v18_ci_inject.py <v17_ci.sh> <v18_ci.sh>')
    s = Path(sys.argv[1]).read_text()

    # Make future fail-closed barriers actionable instead of ending with a bare
    # "Process completed with exit code 1".
    shell_header = 'set -euo pipefail\n'
    shell_header_v18 = '''set -Eeuo pipefail
trap 'rc=$?; echo "::error::v18 pipeline failed at line ${LINENO}: ${BASH_COMMAND} (exit ${rc})" >&2; exit "${rc}"' ERR
'''
    s = replace_once(s, shell_header, shell_header_v18, 'v18 shell diagnostics')

    block = '''  python3 "$BUILDER/scripts/patch_v15_tls_cdtunnel.py" \\
    "$idevice_root/idevice/src/remote_pairing/tunnel.rs" \\
    "$idevice_root/idevice/src/remote_pairing/tls_psk.rs" \\
    "$idevice_root/ffi/src/tunnel_provider.rs"\n'''
    inject = block + '''  python3 "$BUILDER/scripts/patch_v18_full_pipeline.py" \\
    "$idevice_root/idevice/src/remote_pairing/tunnel.rs" \\
    "$idevice_root/idevice/src/remote_pairing/tls_psk.rs" \\
    "$idevice_root/idevice/src/services/rsd.rs"\n'''
    s = replace_once(s, block, inject, 'v18 patch call')

    # V18 renames the post-request CDTunnel markers. The stale V17 source
    # barrier was the exact reason preflight exited after Rust compiled.
    barrier = '''  grep -q '\\[SS-V17-CDT\\] RESPONSE_HEADER_PASS' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"\n'''
    barrier_v18 = '''  grep -q '\\[SS-V18-CDT\\] REQUEST_WRITE_PASS' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"
  grep -q '\\[SS-V18-CDT\\] RESPONSE_HEADER_PASS' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"
  grep -q '\\[SS-V18-CDT\\] HANDSHAKE_PASS' "$idevice_root/idevice/src/remote_pairing/tunnel.rs"
  grep -q '\\[SS-V18-TLS\\] RX_RECORD' "$idevice_root/idevice/src/remote_pairing/tls_psk.rs"
  grep -q '\\[SS-V18-TLS\\] ALERT' "$idevice_root/idevice/src/remote_pairing/tls_psk.rs"
  grep -q '\\[SS-V18-TLS\\] APP_DECRYPT_PASS' "$idevice_root/idevice/src/remote_pairing/tls_psk.rs"
  grep -q '\\[SS-V18-RSD\\] VALIDATION_PASS' "$idevice_root/idevice/src/services/rsd.rs"
'''
    s = replace_once(s, barrier, barrier_v18, 'v18 source barriers')

    py = 'python3 -m py_compile "$BUILDER/scripts/patch_v15_tls_cdtunnel.py"\n'
    s = replace_once(
        s,
        py,
        py + 'python3 -m py_compile "$BUILDER/scripts/patch_v18_full_pipeline.py"\n',
        'v18 py_compile',
    )

    # Replace the stale V17 CDTunnel artifact requirements as one coherent
    # block. Otherwise a valid V18 IPA fails final embedded-marker validation.
    marker_block = '''  '[SS-V17-CDT] HANDSHAKE_START' \\
  '[SS-V17-CDT] REQUEST_WRITE_PASS' \\
  '[SS-V17-CDT] RESPONSE_HEADER_PASS' \\
  '[SS-V17-CDT] HANDSHAKE_PASS' \\
'''
    marker_block_v18 = '''  '[SS-V17-CDT] HANDSHAKE_START' \\
  '[SS-V18-CDT] REQUEST_WRITE_PASS' \\
  '[SS-V18-CDT] RESPONSE_HEADER_PASS' \\
  '[SS-V18-CDT] HANDSHAKE_PASS' \\
  '[SS-V18-TLS] RX_RECORD' \\
  '[SS-V18-TLS] ALERT' \\
  '[SS-V18-TLS] APP_DECRYPT_PASS' \\
  '[SS-V18-RSD] VALIDATION_PASS' \\
'''
    s = replace_once(s, marker_block, marker_block_v18, 'v18 binary marker block')

    s = s.replace('SideStore v17 CDTunnel/RSD preflight', 'SideStore v18 full CDTunnel/RSD/signing preflight')
    s = s.replace('SideStore v17 CDTunnel/RSD Signing', 'SideStore v18 Full CDTunnel RSD Signing')

    out = Path(sys.argv[2])
    out.write_text(s)
    out.chmod(0o755)
    print(f'v18 CI runner written to {out}')


if __name__ == '__main__':
    main()
