#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 4:
    raise SystemExit("usage: run_v11_clean_local_tests.py <builder-root> <minimuxer-root> <emproxy-root>")

builder = Path(sys.argv[1]).resolve()
minimuxer = Path(sys.argv[2]).resolve()
emproxy = Path(sys.argv[3]).resolve()

scripts = [
    builder / "scripts" / "patch_v11_clean_backend.py",
    builder / "scripts" / "patch_v11_clean_libimobile.py",
    builder / "scripts" / "patch_v11_clean_packages.py",
    builder / "scripts" / "apply_emproxy_diag.py",
]
for script in scripts:
    if not script.exists():
        raise SystemExit(f"missing script: {script}")
    subprocess.check_call([sys.executable, "-m", "py_compile", str(script)])

def run(script: Path, target: Path) -> None:
    subprocess.check_call([sys.executable, str(script), str(target)])

api = minimuxer / "Sources" / "MinimuxerApi.swift"
limd = minimuxer / "DeviceGateway" / "libimobiledevice" / "LibimobiledeviceGateway.swift"
emp = emproxy / "src" / "lib.rs"

for _ in range(2):
    run(scripts[0], api)
    run(scripts[1], limd)
    run(scripts[2], minimuxer)
    run(scripts[3], emp)

checks = {
    api: [
        "[SS-V11-CLEAN-RP]",
        "currentBackend: GatewayBackend = .libimobiledevice",
        "let requestedBackend = backend ?? .libimobiledevice",
        "let resolvedBackend: GatewayBackend = .libimobiledevice",
        "previousBackend == resolvedBackend",
        "overriding requested backend=",
    ],
    limd: [
        "CONTROL_PAIR_VERIFY_SUCCESS",
        "DYNAMIC_CANDIDATES",
        "DYNAMIC_CONNECT_START",
        "DYNAMIC_CONNECT_SUCCESS",
        "STRICT_UDID_QUERY_START",
        "STRICT_UDID_QUERY_SUCCESS",
        "ssV11DynamicTunnelHosts",
    ],
    minimuxer / "Package.swift": ['path: "LocalBinary/EMProxy.xcframework"'],
    minimuxer / "DeviceGateway" / "Package.swift": [
        "e72cd0272ab7b4548b5cd22ed4a81008b2b52717",
        'name: "libimobiledevice"',
        'name: "OpenSSL"',
    ],
    emp: [
        "[EMP-NONBLOCK] UDP socket nonblocking mode enabled",
        "[EMP-DIAG] packet diagnostic build active",
    ],
}
for path, required in checks.items():
    text = path.read_text()
    missing = [x for x in required if x not in text]
    if missing:
        raise SystemExit(f"verification failed for {path}: {missing}")

for path, forbidden in {
    api: [
        "currentBackend: GatewayBackend = .idevice",
        "currentBackend == resolvedBackend",
        "let resolvedBackend = backend ?? currentBackend",
    ],
    limd: ["SS-V10-LOCKDOWN-BOOTSTRAP", "SS-V9-COREDEVICE", "SS-ADAPT", "SS-SOURCE-BOUND"],
    minimuxer / "DeviceGateway" / "Package.swift": [
        'path: "LocalBinary/IDevice.xcframework"',
        'path: "LocalBinary/OpenSSL.xcframework"',
        'branch: "main"',
    ],
    emp: ["EMP-NAT46", "EMP-HAIRPIN", "Unable to set UDP timeout", "Rebinding to socket"],
}.items():
    text = path.read_text()
    present = [x for x in forbidden if x in text]
    if present:
        raise SystemExit(f"clean-v11 barrier failed for {path}: {present}")

print("v11 clean RP local structural/idempotence suite PASSED")
