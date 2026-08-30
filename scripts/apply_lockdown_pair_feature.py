#!/usr/bin/env python3
from pathlib import Path
import re
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_lockdown_pair_feature.py <ffi/Cargo.toml>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "# SS-V10-LOCKDOWN-PAIR-FEATURE"


def default_feature_block(text: str) -> tuple[int, int, str]:
    # Match the real TOML assignment at the start of a line. The upstream file
    # also contains a commented example (`# default = [...]`), which must not be
    # mistaken for the active feature list.
    match = re.search(r"(?m)^default\s*=\s*\[", text)
    if not match:
        raise SystemExit("Could not locate active Cargo default feature list")
    start = match.start()
    end_match = re.search(r"(?m)^\]", text[match.end():])
    if not end_match:
        raise SystemExit("Could not locate end of active Cargo default feature list")
    end = match.end() + end_match.start()
    return start, end, text[start:end]


def apply_v11_split_provider() -> None:
    script = Path(__file__).with_name("apply_split_coredevice_provider.py")
    provider_rs = p.parent / "src/provider.rs"
    core_proxy_rs = p.parent.parent / "idevice/src/services/core_device_proxy.rs"
    for required in [script, provider_rs, core_proxy_rs]:
        if not required.exists():
            raise SystemExit(f"Missing required v11 split-provider input: {required}")
    subprocess.check_call(
        [sys.executable, str(script), str(provider_rs), str(core_proxy_rs)]
    )
    provider_text = provider_rs.read_text()
    proxy_text = core_proxy_rs.read_text()
    required_markers = [
        (provider_text, "idevice_sidestore_split_tcp_provider_new"),
        (provider_text, "[SS-V11-SPLIT-PROVIDER] SERVICE_PLAN"),
        (proxy_text, "[SS-V11-COREDEVICE] CDTUNNEL_SUCCESS"),
    ]
    missing = [marker for text, marker in required_markers if marker not in text]
    if missing:
        raise SystemExit(f"v11 split-provider companion verification failed: {missing}")


feature_decl = 'pair = ["idevice/pair"]'
if feature_decl not in s:
    raise SystemExit("IDevice FFI pair feature declaration is missing")

_, _, block = default_feature_block(s)
if marker in s:
    if '"pair"' not in block:
        raise SystemExit("v10 pair marker exists but pair is absent from active default features")
    apply_v11_split_provider()
    print("Lockdown pair feature + v11 split provider already enabled and verified")
    raise SystemExit(0)

if '"pair"' not in block:
    # Insert immediately after the active list's opening line. This avoids
    # depending on the last feature's comma style or exact ordering.
    s, count = re.subn(
        r"(?m)^(default\s*=\s*\[\s*\n)",
        r'\1  "pair",\n',
        s,
        count=1,
    )
    if count != 1:
        raise SystemExit("Failed to insert pair into active Cargo default features")

# Keep an explicit marker outside the TOML arrays so repeated patching is deterministic.
s = s.rstrip() + f"\n\n{marker}\n"
p.write_text(s)

_, _, patched_block = default_feature_block(p.read_text())
if '"pair"' not in patched_block:
    raise SystemExit("Failed to enable pair in active Cargo default features")

apply_v11_split_provider()
print("Enabled IDevice lockdown pairing feature and v11 split provider")
