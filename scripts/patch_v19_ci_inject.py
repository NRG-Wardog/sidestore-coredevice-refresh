#!/usr/bin/env python3
"""Layer the v19 NAT44 runtime fix and low-credit CI policy onto v18."""

from pathlib import Path
import sys


def die(message: str) -> None:
    raise SystemExit(message)


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        die(f"{label} anchor count={count}")
    return source.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: patch_v19_ci_inject.py <v18_ci.sh> <v19_ci.sh>")

    source = Path(sys.argv[1]).read_text()

    preflight_patch = '  python3 "$BUILDER/scripts/patch_v13_emproxy_payload_diag.py" em_proxy/src/lib.rs\n'
    source = replace_once(
        source,
        preflight_patch,
        preflight_patch
        + '  python3 "$BUILDER/scripts/apply_emproxy_nat44_dynamic.py" em_proxy/src/lib.rs\n',
        "v19 preflight EMProxy NAT44 call",
    )

    build_patch = '  python3 "$BUILDER/scripts/patch_v13_emproxy_payload_diag.py" src/lib.rs\n'
    source = replace_once(
        source,
        build_patch,
        build_patch
        + '  python3 "$BUILDER/scripts/apply_emproxy_nat44_dynamic.py" src/lib.rs\n',
        "v19 build EMProxy NAT44 call",
    )

    compile_anchor = 'python3 -m py_compile "$BUILDER/scripts/patch_v18_full_pipeline.py"\n'
    source = replace_once(
        source,
        compile_anchor,
        compile_anchor
        + 'python3 -m py_compile "$BUILDER/scripts/apply_emproxy_nat44_dynamic.py"\n',
        "v19 generator compile barrier",
    )

    source_barrier = '''  ! grep -Eq 'EMP-V13-HAIRPIN|EMP-HAIRPIN|EMP-NAT46|physical-interface bridge active' "$emproxy"\n'''
    source_barrier_v19 = '''  grep -q '\\[EMP-NAT44\\] dynamic listener bridge active' "$emproxy"
  grep -q 'nat44_translate_forward' "$emproxy"
  grep -q 'nat44_translate_reverse' "$emproxy"
  ! grep -Eq 'EMP-V13-HAIRPIN|EMP-HAIRPIN|EMP-NAT46|physical-interface bridge active' "$emproxy"
'''
    source = replace_once(
        source,
        source_barrier,
        source_barrier_v19,
        "v19 NAT44 source barriers",
    )

    marker_anchor = '''  '[EMP-V13-PAYLOAD]' \\
'''
    marker_v19 = marker_anchor + '''  '[EMP-NAT44] dynamic listener bridge active' \\
  'TX-NAT44-FWD' \\
  'TX-NAT44-REV' \\
'''
    source = replace_once(
        source,
        marker_anchor,
        marker_v19,
        "v19 embedded NAT44 markers",
    )

    # The old preflight recompiles the full patched IDevice graph even though
    # the final macOS job compiles it again. For v19 only EMProxy changed, so a
    # FAST_PREFLIGHT run applies every patch and cargo-checks the changed Rust
    # crate, while preserving the original full gate for manual deep checks.
    cargo_block = '''  cargo test --locked --manifest-path idevice/idevice/Cargo.toml --lib \\
    v13_frame_prefix_round_trip --no-default-features --features 'openssl,tcp'
  cargo check --locked --manifest-path idevice/ffi/Cargo.toml --features obfuscate
  cargo check --locked --manifest-path em_proxy/Cargo.toml --lib
'''
    cargo_block_v19 = '''  if [[ "${FAST_PREFLIGHT:-0}" == "1" ]]; then
    echo 'v19 fast preflight: compile changed EMProxy crate only'
    CARGO_TARGET_DIR="${RUNNER_TEMP:-/tmp}/sidestore-v19-fast-preflight-target" \\
      cargo check --locked --manifest-path em_proxy/Cargo.toml --lib
  else
    cargo test --locked --manifest-path idevice/idevice/Cargo.toml --lib \\
      v13_frame_prefix_round_trip --no-default-features --features 'openssl,tcp'
    cargo check --locked --manifest-path idevice/ffi/Cargo.toml --features obfuscate
    cargo check --locked --manifest-path em_proxy/Cargo.toml --lib
  fi
'''
    source = replace_once(
        source,
        cargo_block,
        cargo_block_v19,
        "v19 fast preflight cargo gate",
    )

    source = source.replace(
        "SideStore v18 full CDTunnel/RSD/signing preflight",
        "SideStore v19 NAT44 dynamic-listener preflight",
    )
    source = source.replace(
        "SideStore v18 Full CDTunnel RSD Signing",
        "SideStore v19 NAT44 Dynamic Listener",
    )

    preflight_pass = "    echo 'preflight=PASS'\n"
    if preflight_pass in source:
        source = source.replace(
            preflight_pass,
            "    echo 'v19_fix=EMProxy NAT44 dynamic listener bridge; no physical self-connect'\n"
            + preflight_pass,
            1,
        )

    verification_pass = "  echo 'verification=PASS'\n"
    if verification_pass in source:
        source = source.replace(
            verification_pass,
            "  echo 'v19_fix=EMProxy NAT44 dynamic listener bridge; no physical self-connect'\n"
            + verification_pass,
            1,
        )

    output = Path(sys.argv[2])
    output.write_text(source)
    output.chmod(0o755)
    print(f"v19 CI runner written to {output}")


if __name__ == "__main__":
    main()
