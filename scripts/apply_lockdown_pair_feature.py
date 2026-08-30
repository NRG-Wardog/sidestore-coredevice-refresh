#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_lockdown_pair_feature.py <ffi/Cargo.toml>")

p = Path(sys.argv[1])
s = p.read_text()

marker = "# SS-V10-LOCKDOWN-PAIR-FEATURE"
if marker in s:
    default_start = s.find("default = [")
    if default_start < 0:
        raise SystemExit("Cargo default feature list missing")
    default_end = s.find("\n]", default_start)
    default_block = s[default_start:default_end]
    if '"pair"' not in default_block:
        raise SystemExit("v10 pair marker exists but pair is absent from default features")
    print("Lockdown pair feature already enabled and verified")
    raise SystemExit(0)

feature_decl = 'pair = ["idevice/pair"]'
if feature_decl not in s:
    raise SystemExit("IDevice FFI pair feature declaration is missing")

default_start = s.find("default = [")
if default_start < 0:
    raise SystemExit("Could not locate Cargo default feature list")
default_end = s.find("\n]", default_start)
if default_end < 0:
    raise SystemExit("Could not locate end of Cargo default feature list")
default_block = s[default_start:default_end]

if '"pair"' not in default_block:
    anchor = '  "house_arrest"\n'
    absolute_anchor = s.find(anchor, default_start, default_end)
    if absolute_anchor < 0:
        raise SystemExit("Could not locate house_arrest anchor in Cargo default features")
    insert_at = absolute_anchor
    s = s[:insert_at] + '  "pair",\n' + s[insert_at:]

# Keep an explicit marker outside the TOML arrays so repeated patching is deterministic.
s += f"\n{marker}\n"
p.write_text(s)

patched = p.read_text()
default_start = patched.find("default = [")
default_end = patched.find("\n]", default_start)
default_block = patched[default_start:default_end]
if '"pair"' not in default_block:
    raise SystemExit("Failed to enable pair in Cargo default features")

print("Enabled IDevice lockdown pairing feature for the iOS FFI build")
