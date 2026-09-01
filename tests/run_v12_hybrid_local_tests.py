#!/usr/bin/env python3
"""Structural, idempotence, ownership, negative, and upstream-contract tests for v12."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def run(*args: str, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if expect_success and result.returncode != 0:
        die(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}")
    if not expect_success and result.returncode == 0:
        die(f"negative test unexpectedly succeeded: {' '.join(args)}\n{result.stdout}")
    return result


def digest(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(str(path).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def main() -> None:
    if len(sys.argv) != 6:
        die(
            "usage: run_v12_hybrid_local_tests.py "
            "<builder-root> <minimuxer-root> <em-proxy-root> <idevice-root> <sidestore-root>"
        )

    builder, minimuxer_src, emproxy_src, idevice_src, sidestore_src = map(Path, sys.argv[1:])
    scripts = builder / "scripts"
    tests = builder / "tests"
    required_inputs = [
        scripts / "patch_v12_hybrid_backend.py",
        scripts / "patch_v12_hybrid_idevice.py",
        scripts / "patch_v12_packages.py",
        scripts / "apply_v12_boot_transport_fix.py",
        scripts / "apply_emproxy_diag.py",
        tests / "verify_v12_upstream_contract.py",
        minimuxer_src / "DeviceGateway" / "idevice" / "IdeviceGateway.swift",
        sidestore_src / "SideStore" / "AppBootManager.swift",
    ]
    missing = [str(path) for path in required_inputs if not path.exists()]
    if missing:
        die(f"missing v12 test inputs: {missing}")

    for script in required_inputs[:6]:
        run(sys.executable, "-m", "py_compile", str(script))
    run(sys.executable, str(tests / "verify_v12_upstream_contract.py"), str(idevice_src))

    with tempfile.TemporaryDirectory(prefix="sidestore-v12-tests-") as tmp_name:
        tmp = Path(tmp_name)
        minimuxer = tmp / "minimuxer"
        emproxy = tmp / "em_proxy"
        fake_sidestore = tmp / "SideStore"
        shutil.copytree(minimuxer_src, minimuxer, ignore=shutil.ignore_patterns(".git", ".build"))
        shutil.copytree(emproxy_src, emproxy, ignore=shutil.ignore_patterns(".git", "target"))
        (fake_sidestore / "SideStore").mkdir(parents=True)
        shutil.copy2(sidestore_src / "SideStore" / "AppBootManager.swift", fake_sidestore / "SideStore" / "AppBootManager.swift")
        (fake_sidestore / "Dependencies").mkdir(parents=True)
        shutil.copytree(minimuxer, fake_sidestore / "Dependencies" / "minimuxer")

        gateway = minimuxer / "DeviceGateway" / "idevice" / "IdeviceGateway.swift"
        backend = minimuxer / "Sources" / "MinimuxerApi.swift"
        package = minimuxer / "Package.swift"
        gateway_package = minimuxer / "DeviceGateway" / "Package.swift"
        network = minimuxer / "Sources" / "Services" / "NetworkObserverService.swift"
        emproxy_lib = emproxy / "src" / "lib.rs"
        fake_gateway = fake_sidestore / "Dependencies" / "minimuxer" / "DeviceGateway" / "idevice" / "IdeviceGateway.swift"
        fake_backend = fake_sidestore / "Dependencies" / "minimuxer" / "Sources" / "MinimuxerApi.swift"
        fake_package = fake_sidestore / "Dependencies" / "minimuxer" / "Package.swift"
        fake_gateway_package = fake_sidestore / "Dependencies" / "minimuxer" / "DeviceGateway" / "Package.swift"
        fake_network = fake_sidestore / "Dependencies" / "minimuxer" / "Sources" / "Services" / "NetworkObserverService.swift"
        app_boot = fake_sidestore / "SideStore" / "AppBootManager.swift"

        pristine_gateway = gateway.read_text()
        pristine_backend = backend.read_text()

        patch_commands = [
            (sys.executable, str(scripts / "patch_v12_hybrid_backend.py"), str(backend)),
            (sys.executable, str(scripts / "patch_v12_hybrid_idevice.py"), str(gateway)),
            (sys.executable, str(scripts / "patch_v12_packages.py"), str(minimuxer)),
            (sys.executable, str(scripts / "apply_emproxy_diag.py"), str(emproxy_lib)),
        ]
        for command in patch_commands:
            run(*command)

        # Mirror patched minimuxer files into the fake SideStore tree before boot verification.
        for source, target in [
            (gateway, fake_gateway),
            (backend, fake_backend),
            (package, fake_package),
            (gateway_package, fake_gateway_package),
            (network, fake_network),
        ]:
            shutil.copy2(source, target)
        run(sys.executable, str(scripts / "apply_v12_boot_transport_fix.py"), str(app_boot))

        tracked = [gateway, backend, package, gateway_package, network, emproxy_lib, app_boot]
        first_hash = digest(tracked)
        for command in patch_commands:
            run(*command)
        run(sys.executable, str(scripts / "apply_v12_boot_transport_fix.py"), str(app_boot))
        second_hash = digest(tracked)
        if first_hash != second_hash:
            die("v12 patch set is not idempotent")

        gateway_text = gateway.read_text()
        backend_text = backend.read_text()
        package_text = package.read_text()
        gateway_package_text = gateway_package.read_text()
        network_text = network.read_text()
        emproxy_text = emproxy_lib.read_text()
        boot_text = app_boot.read_text()

        required_gateway = [
            "HYBRID_FILE has_rp=",
            "has_lockdown=",
            "LOCKDOWN_PAIRING_PARSE_SUCCESS",
            "idevice_tcp_provider_new",
            "tunnel_create_usb",
            "COREDEVICE_TUNNEL_SUCCESS",
            "RP_FALLBACK_START",
            "dynamic_route=same_endpoint_first_and_only",
            "STRICT_UDID_QUERY_SUCCESS",
        ]
        missing = [item for item in required_gateway if item not in gateway_text]
        if missing:
            die(f"patched gateway missing expected architecture: {missing}")

        core_index = gateway_text.find("TRANSPORT_SELECT path=existing-lockdown-coredevice-proxy")
        rp_index = gateway_text.find("TRANSPORT_SELECT path=exact-endpoint-remote-pairing-fallback")
        if core_index < 0 or rp_index < 0 or core_index >= rp_index:
            die("transport order is wrong: existing Lockdown/CoreDeviceProxy must precede RP fallback")

        if gateway_text.count("tunnel_create_usb(") != 1 or gateway_text.count("tunnel_create_rppairing(") != 1:
            die("gateway must contain exactly one CoreDevice and one RP tunnel constructor")

        forbidden_gateway = [
            "lockdownd_pair(",
            "PAIR_REQUEST_START",
            "DYNAMIC_CANDIDATES",
            "SS-V11-CLEAN-RP",
            "SS-V10-LOCKDOWN-BOOTSTRAP",
            "SS-V9-COREDEVICE",
            "SS-ADAPT",
            "getRustPlistString returned UDID:",
        ]
        leaked = [item for item in forbidden_gateway if item in gateway_text]
        if leaked:
            die(f"legacy/unsafe gateway logic leaked into v12: {leaked}")

        if "let resolvedBackend: GatewayBackend = .idevice" not in backend_text:
            die("v12 backend is not forced to Idevice")
        if "previousBackend == resolvedBackend" not in backend_text:
            die("backend cache lifecycle bug was not fixed")
        if 'path: "LocalBinary/EMProxy.xcframework"' not in package_text:
            die("fixed local EMProxy is not selected")
        if "v0.1.66-ss-61c2704" not in gateway_package_text:
            die("official exact IDevice release is not pinned")
        if 'path: "LocalBinary/IDevice.xcframework"' in gateway_package_text:
            die("v12 unexpectedly injects an unverified local IDevice binary")
        if "retaining explicit LocalVPN peer" not in network_text:
            die("explicit peer convergence protection missing")
        if "EMP-NONBLOCK" not in emproxy_text:
            die("EMProxy nonblocking fix missing")
        for forbidden in ["Unable to set UDP timeout", "Rebinding to socket", "EMP-NAT46", "EMP-HAIRPIN"]:
            if forbidden in emproxy_text:
                die(f"forbidden EMProxy logic remains: {forbidden}")
        if "guard let deviceUDID = fetchedUDID, !deviceUDID.isEmpty" not in boot_text:
            die("strict boot live-UDID gate missing")
        if "PAIRING FILE IS VALID but TRANSPORT FAILED" not in boot_text:
            die("boot no longer distinguishes pairing material from transport failure")

        # Negative compatibility tests: source drift must fail closed, never fuzzy-patch.
        bad_gateway = tmp / "bad-IdeviceGateway.swift"
        bad_gateway.write_text(pristine_gateway.replace("    private func ensureRPConnection() throws {", "    private func ensureChangedConnection() throws {", 1))
        run(sys.executable, str(scripts / "patch_v12_hybrid_idevice.py"), str(bad_gateway), expect_success=False)

        bad_backend = tmp / "bad-MinimuxerApi.swift"
        bad_backend.write_text(pristine_backend.replace("        let resolvedBackend = backend ?? currentBackend", "        let backendWasRefactored = true", 1))
        run(sys.executable, str(scripts / "patch_v12_hybrid_backend.py"), str(bad_backend), expect_success=False)

        print("v12 local suite PASS")
        print(f"  idempotence_sha256={first_hash}")
        print("  hybrid capability parsing + Lockdown-first order verified")
        print("  no on-device Pair request, no candidate fan-out, no legacy markers")
        print("  exact upstream IDevice StartSession/CoreDeviceProxy contract verified")
        print("  EMProxy nonblocking and strict live-UDID boot gate verified")


if __name__ == "__main__":
    main()
