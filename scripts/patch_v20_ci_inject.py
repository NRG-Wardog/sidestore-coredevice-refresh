#!/usr/bin/env python3
'''Layer the v20 IKEv2-first transit policy and low-credit CI gates onto v18.'''

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
        die("usage: patch_v20_ci_inject.py <v18_ci.sh> <v20_ci.sh>")

    source = Path(sys.argv[1]).read_text()

    preflight_patch = '  python3 "$BUILDER/scripts/patch_v13_emproxy_payload_diag.py" em_proxy/src/lib.rs\n'
    source = replace_once(
        source,
        preflight_patch,
        preflight_patch
        + '  python3 "$BUILDER/scripts/apply_emproxy_nat44_dynamic.py" em_proxy/src/lib.rs\n'
        + '  python3 "$BUILDER/scripts/apply_emproxy_ikev2_transit.py" em_proxy/src/lib.rs\n',
        "v20 preflight EMProxy transit call",
    )

    build_patch = '  python3 "$BUILDER/scripts/patch_v13_emproxy_payload_diag.py" src/lib.rs\n'
    source = replace_once(
        source,
        build_patch,
        build_patch
        + '  python3 "$BUILDER/scripts/apply_emproxy_nat44_dynamic.py" src/lib.rs\n'
        + '  python3 "$BUILDER/scripts/apply_emproxy_ikev2_transit.py" src/lib.rs\n',
        "v20 build EMProxy transit call",
    )

    compile_anchor = 'python3 -m py_compile "$BUILDER/scripts/patch_v18_full_pipeline.py"\n'
    source = replace_once(
        source,
        compile_anchor,
        compile_anchor
        + 'python3 -m py_compile "$BUILDER/scripts/apply_emproxy_nat44_dynamic.py"\n'
        + 'python3 -m py_compile "$BUILDER/scripts/apply_emproxy_ikev2_transit.py"\n',
        "v20 generator compile barrier",
    )

    source_barrier = '''  ! grep -Eq 'EMP-V13-HAIRPIN|EMP-HAIRPIN|EMP-NAT46|physical-interface bridge active' "$emproxy"\n'''
    source_barrier_v20 = '''  grep -q '\\[EMP-NAT44\\] dynamic listener bridge active' "$emproxy"
  grep -q '\\[EMP-TRANSIT\\] selected interface=' "$emproxy"
  grep -q 'transit=ipsec-first,en0-fallback' "$emproxy"
  grep -q 'nat44_transit_ipv4' "$emproxy"
  grep -q 'name.starts_with("ipsec")' "$emproxy"
  grep -q 'nat44_translate_forward' "$emproxy"
  grep -q 'nat44_translate_reverse' "$emproxy"
  ! grep -q 'fn nat44_en0_ipv4' "$emproxy"
  ! grep -Fq 'source=en0/en*' "$emproxy"
  ! grep -Eq 'EMP-V13-HAIRPIN|EMP-HAIRPIN|EMP-NAT46|physical-interface bridge active' "$emproxy"
'''
    source = replace_once(
        source,
        source_barrier,
        source_barrier_v20,
        "v20 IKEv2-first source barriers",
    )

    marker_anchor = '''  '[EMP-V13-PAYLOAD]' \\\n'''
    marker_v20 = marker_anchor + '''  '[EMP-NAT44] dynamic listener bridge active' \\
  '[EMP-TRANSIT] selected interface=' \\
  'transit=ipsec-first,en0-fallback' \\
  'TX-NAT44-FWD' \\
  'TX-NAT44-REV' \\
'''
    source = replace_once(
        source,
        marker_anchor,
        marker_v20,
        "v20 embedded transit markers",
    )

    # The final macOS job compiles the complete graph. The Linux preflight
    # applies every source patch but cargo-checks only EMProxy, the component
    # changed by v20.
    cargo_block = '''  cargo test --locked --manifest-path idevice/idevice/Cargo.toml --lib \\
    v13_frame_prefix_round_trip --no-default-features --features 'openssl,tcp'
  cargo check --locked --manifest-path idevice/ffi/Cargo.toml --features obfuscate
  cargo check --locked --manifest-path em_proxy/Cargo.toml --lib
'''
    cargo_block_v20 = '''  if [[ "${FAST_PREFLIGHT:-0}" == "1" ]]; then
    echo 'v20 fast preflight: compile changed EMProxy crate only'
    CARGO_TARGET_DIR="${RUNNER_TEMP:-/tmp}/sidestore-v20-fast-preflight-target" \\
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
        cargo_block_v20,
        "v20 fast preflight cargo gate",
    )

    # The pinned IDevice build uses cbindgen as a Rust library. Installing the
    # standalone bindgen executable wastes macOS minutes and is not consumed.
    tooling_block = '''brew install ldid xcbeautify || true
if ! command -v bindgen >/dev/null 2>&1; then
  cargo install --locked bindgen-cli --version 0.72.1
fi
rustup target add aarch64-apple-ios
'''
    tooling_block_v20 = '''if ! command -v ldid >/dev/null 2>&1; then
  brew install ldid
fi
if ! command -v xcbeautify >/dev/null 2>&1; then
  brew install xcbeautify
fi
rustup target add aarch64-apple-ios
'''
    source = replace_once(
        source,
        tooling_block,
        tooling_block_v20,
        "v20 remove unused bindgen-cli install",
    )

    source = source.replace(
        "SideStore v18 full CDTunnel/RSD/signing preflight",
        "SideStore v20 IKEv2-first transit preflight",
    )
    source = source.replace(
        "SideStore v18 Full CDTunnel RSD Signing",
        "SideStore v20 IKEv2 First Transit",
    )

    preflight_pass = "    echo 'preflight=PASS'\n"
    if preflight_pass in source:
        source = source.replace(
            preflight_pass,
            "    echo 'v20_fix=dynamic IPv4 transit selects active ipsec* before en0'\n"
            + preflight_pass,
            1,
        )

    verification_pass = "  echo 'verification=PASS'\n"
    if verification_pass in source:
        source = source.replace(
            verification_pass,
            "  echo 'v20_fix=dynamic IPv4 transit selects active ipsec* before en0'\n"
            + verification_pass,
            1,
        )

    output = Path(sys.argv[2])
    output.write_text(source)
    output.chmod(0o755)
    print(f"v20 CI runner written to {output}")


if __name__ == "__main__":
    main()
