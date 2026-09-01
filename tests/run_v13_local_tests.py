#!/usr/bin/env python3
"""Fail-closed preflight suite for SideStore v13.

The suite first re-runs the complete v12 tests, then applies v12 once and v13
twice to exact source copies.  It validates plist framing, QueryType ordering,
FFI serialization, ownership, package selection, bounded diagnostics, privacy,
and source-drift failures before a macOS runner is allowed to build an IPA.
"""

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
    hasher = hashlib.sha256()
    for path in sorted(paths):
        hasher.update(str(path).encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def main() -> None:
    if len(sys.argv) != 6:
        die(
            "usage: run_v13_local_tests.py "
            "<builder-root> <minimuxer-root> <em-proxy-root> <idevice-root> <sidestore-root>"
        )

    builder, minimuxer_src, emproxy_src, idevice_src, sidestore_src = map(Path, sys.argv[1:])
    scripts = builder / "scripts"
    tests = builder / "tests"
    v13_scripts = [
        scripts / "patch_v13_idevice_protocol.py",
        scripts / "patch_v13_swift_serialization.py",
        scripts / "patch_v13_emproxy_payload_diag.py",
        scripts / "patch_v13_packages.py",
    ]
    v12_scripts = [
        scripts / "patch_v12_hybrid_backend.py",
        scripts / "patch_v12_hybrid_idevice.py",
        scripts / "patch_v12_packages.py",
        scripts / "apply_v12_boot_transport_fix.py",
        scripts / "apply_emproxy_diag.py",
    ]
    required = v13_scripts + v12_scripts + [
        tests / "run_v12_hybrid_local_tests.py",
        tests / "verify_v12_upstream_contract.py",
        minimuxer_src / "DeviceGateway" / "idevice" / "IdeviceGateway.swift",
        idevice_src / "idevice" / "src" / "lib.rs",
        idevice_src / "idevice" / "src" / "provider.rs",
        idevice_src / "ffi" / "src" / "tunnel_provider.rs",
        emproxy_src / "src" / "lib.rs",
        sidestore_src / "SideStore" / "AppBootManager.swift",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        die(f"missing v13 test input(s): {missing}")

    for script in v13_scripts + v12_scripts + [
        tests / "run_v12_hybrid_local_tests.py",
        tests / "verify_v12_upstream_contract.py",
    ]:
        run(sys.executable, "-m", "py_compile", str(script))

    # Preserve every v12 architecture and safety invariant before layering v13.
    v12_result = run(
        sys.executable,
        str(tests / "run_v12_hybrid_local_tests.py"),
        str(builder),
        str(minimuxer_src),
        str(emproxy_src),
        str(idevice_src),
        str(sidestore_src),
    )
    if "v12 local suite PASS" not in v12_result.stdout:
        die("nested v12 suite did not report PASS")

    with tempfile.TemporaryDirectory(prefix="sidestore-v13-tests-") as temp_name:
        temp = Path(temp_name)
        minimuxer = temp / "minimuxer"
        emproxy = temp / "em_proxy"
        idevice = temp / "idevice"
        fake_sidestore = temp / "SideStore"
        shutil.copytree(minimuxer_src, minimuxer, ignore=shutil.ignore_patterns(".git", ".build"))
        shutil.copytree(emproxy_src, emproxy, ignore=shutil.ignore_patterns(".git", "target"))
        shutil.copytree(idevice_src, idevice, ignore=shutil.ignore_patterns(".git", "target"))
        (fake_sidestore / "SideStore").mkdir(parents=True)
        shutil.copy2(
            sidestore_src / "SideStore" / "AppBootManager.swift",
            fake_sidestore / "SideStore" / "AppBootManager.swift",
        )
        (fake_sidestore / "Dependencies").mkdir(parents=True)
        shutil.copytree(minimuxer, fake_sidestore / "Dependencies" / "minimuxer")

        gateway = minimuxer / "DeviceGateway" / "idevice" / "IdeviceGateway.swift"
        backend = minimuxer / "Sources" / "MinimuxerApi.swift"
        package = minimuxer / "Package.swift"
        gateway_package = minimuxer / "DeviceGateway" / "Package.swift"
        network = minimuxer / "Sources" / "Services" / "NetworkObserverService.swift"
        emproxy_lib = emproxy / "src" / "lib.rs"
        idevice_lib = idevice / "idevice" / "src" / "lib.rs"
        idevice_provider = idevice / "idevice" / "src" / "provider.rs"
        tunnel_provider = idevice / "ffi" / "src" / "tunnel_provider.rs"
        app_boot = fake_sidestore / "SideStore" / "AppBootManager.swift"

        pristine_idevice_lib = idevice_lib.read_text()
        pristine_gateway = gateway.read_text()
        pristine_emproxy = emproxy_lib.read_text()

        # v12 base is applied once.  Boot validation runs before v13 changes its
        # architecture markers, exactly matching the production workflow order.
        run(sys.executable, str(scripts / "patch_v12_hybrid_backend.py"), str(backend))
        run(sys.executable, str(scripts / "patch_v12_hybrid_idevice.py"), str(gateway))
        run(sys.executable, str(scripts / "patch_v12_packages.py"), str(minimuxer))
        run(sys.executable, str(scripts / "apply_emproxy_diag.py"), str(emproxy_lib))

        for source, target in [
            (gateway, fake_sidestore / "Dependencies" / "minimuxer" / "DeviceGateway" / "idevice" / "IdeviceGateway.swift"),
            (backend, fake_sidestore / "Dependencies" / "minimuxer" / "Sources" / "MinimuxerApi.swift"),
            (package, fake_sidestore / "Dependencies" / "minimuxer" / "Package.swift"),
            (gateway_package, fake_sidestore / "Dependencies" / "minimuxer" / "DeviceGateway" / "Package.swift"),
            (network, fake_sidestore / "Dependencies" / "minimuxer" / "Sources" / "Services" / "NetworkObserverService.swift"),
        ]:
            shutil.copy2(source, target)
        run(sys.executable, str(scripts / "apply_v12_boot_transport_fix.py"), str(app_boot))

        v13_commands = [
            (sys.executable, str(scripts / "patch_v13_idevice_protocol.py"), str(idevice)),
            (sys.executable, str(scripts / "patch_v13_swift_serialization.py"), str(gateway)),
            (sys.executable, str(scripts / "patch_v13_emproxy_payload_diag.py"), str(emproxy_lib)),
            (sys.executable, str(scripts / "patch_v13_packages.py"), str(minimuxer)),
        ]
        for command in v13_commands:
            run(*command)

        tracked = [
            gateway,
            backend,
            package,
            gateway_package,
            network,
            emproxy_lib,
            idevice_lib,
            idevice_provider,
            tunnel_provider,
            app_boot,
        ]
        first_hash = digest(tracked)
        for command in v13_commands:
            run(*command)
        second_hash = digest(tracked)
        if first_hash != second_hash:
            die("v13 patch set is not idempotent")

        lib_text = idevice_lib.read_text()
        provider_text = idevice_provider.read_text()
        tunnel_text = tunnel_provider.read_text()
        gateway_text = gateway.read_text()
        emproxy_text = emproxy_lib.read_text()
        gateway_package_text = gateway_package.read_text()
        boot_text = app_boot.read_text()

        # Framing: one buffer, one write, big-endian length before payload.
        if lib_text.count("socket.write_all(&framed).await?") != 2:
            die("v13 framing invariant failed: XML and binary plist must each use one write")
        for stale in [
            "socket.write_all(&len.to_be_bytes()).await?",
            "socket.write_all(message.as_bytes()).await?",
            "socket.write_all(&message).await?",
        ]:
            if stale in lib_text:
                die(f"v13 framing invariant failed: split write remains: {stale}")
        prefix_index = lib_text.find("framed.extend_from_slice(&(payload.len() as u32).to_be_bytes())")
        payload_index = lib_text.find("framed.extend_from_slice(payload)")
        if prefix_index < 0 or payload_index < 0 or prefix_index >= payload_index:
            die("v13 framing invariant failed: big-endian prefix must precede payload")
        if "v13_frame_prefix_round_trip" not in lib_text:
            die("v13 framing unit test missing")
        if "stream.set_nodelay(true)?" not in provider_text:
            die("v13 TCP_NODELAY invariant missing")

        # Protocol: canonical QueryType precedes GetValue/StartSession and service creation.
        ordered = [
            "LOCKDOWN_TCP_CONNECT_START",
            "LOCKDOWN_QUERY_TYPE_START",
            "LOCKDOWN_START_SESSION_START",
            "COREDEVICE_START_SERVICE_START",
            "COREDEVICE_SERVICE_CONNECT_START",
            "COREDEVICE_CDTUNNEL_START",
            "COREDEVICE_RSD_CONNECT_START",
            "COREDEVICE_RSD_HANDSHAKE_SUCCESS",
        ]
        positions = [tunnel_text.find(item) for item in ordered]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            die(f"v13 protocol order invalid: {positions}")
        if "CoreDeviceProxy::connect(provider_ref)" in tunnel_text[
            tunnel_text.find('pub unsafe extern "C" fn tunnel_create_usb('):
            tunnel_text.find("/// Pairs via USB CoreDeviceProxy")
        ]:
            die("v13 explicit QueryType-first tunnel was bypassed by generic CoreDevice connect")

        # Concurrency: all async gateway FFI calls use one serial queue.
        serial_count = gateway_text.count("withFFIDispatch(on: Self.v13FFIQueue)")
        if serial_count < 10 or "withFFIDispatch {" in gateway_text:
            die(f"v13 FFI serialization incomplete: serial_count={serial_count}")
        if "native IDevice error diagnostics active" not in gateway_text:
            die("v13 native stage diagnostics are not retained in release builds")

        # Transport architecture inherited from v12 remains intact.
        for required_marker in [
            "HYBRID_FILE has_rp=",
            "has_lockdown=",
            "TRANSPORT_SELECT path=existing-lockdown-coredevice-proxy",
            "RP_FALLBACK_START",
            "dynamic_route=same_endpoint_first_and_only",
            "STRICT_UDID_QUERY_SUCCESS",
        ]:
            if required_marker not in gateway_text:
                die(f"v13 lost v12 transport invariant: {required_marker}")
        if 'path: "LocalBinary/IDevice.xcframework"' not in gateway_package_text:
            die("v13 package does not select patched local IDevice")
        if "[EMP-V13-PAYLOAD]" not in emproxy_text:
            die("v13 bounded payload-length diagnostics missing")
        for required_diag in ["V13_LOCKDOWN_EVENT_COUNTER", "V13_PEER_EVENT_COUNTER", "event_limit"]:
            if required_diag not in emproxy_text:
                die(f"v13 EMProxy budget invariant missing: {required_diag}")

        forbidden_all = [
            "lockdownd_pair(",
            "PAIR_REQUEST_START",
            "trust_prompt_expected=true",
            "DYNAMIC_CANDIDATES",
            "SS-V11-CLEAN-RP",
            "SS-V10-LOCKDOWN-BOOTSTRAP",
            "SS-V9-COREDEVICE",
            "SS-ADAPT",
            "EMP-NAT46",
            "EMP-HAIRPIN",
            "getRustPlistString returned UDID:",
        ]
        combined = gateway_text + "\n" + tunnel_text + "\n" + emproxy_text
        leaked = [item for item in forbidden_all if item in combined]
        if leaked:
            die(f"v13 legacy/unsafe code leaked: {leaked}")
        for payload_leak in ["hex::encode", "from_utf8_lossy(b)", "payload_bytes", "packet_body"]:
            if payload_leak in emproxy_text:
                die(f"v13 packet payload privacy failure: {payload_leak}")
        if "guard let deviceUDID = fetchedUDID, !deviceUDID.isEmpty" not in boot_text:
            die("v13 strict live-UDID boot gate missing")

        # Negative tests: exact source drift must stop the build.
        bad_idevice = temp / "bad-idevice"
        shutil.copytree(idevice_src, bad_idevice, ignore=shutil.ignore_patterns(".git", "target"))
        bad_lib = bad_idevice / "idevice" / "src" / "lib.rs"
        bad_lib.write_text(
            pristine_idevice_lib.replace(
                "            socket.write_all(&len.to_be_bytes()).await?;",
                "            socket.write_all(&legacy_header).await?;",
                1,
            )
        )
        run(
            sys.executable,
            str(scripts / "patch_v13_idevice_protocol.py"),
            str(bad_idevice),
            expect_success=False,
        )

        bad_gateway = temp / "bad-IdeviceGateway.swift"
        bad_gateway.write_text(
            pristine_gateway.replace(
                "    public static let shared = IdeviceGateway()",
                "    public static let singleton = IdeviceGateway()",
                1,
            )
        )
        run(
            sys.executable,
            str(scripts / "patch_v13_swift_serialization.py"),
            str(bad_gateway),
            expect_success=False,
        )

        bad_emproxy = temp / "bad-emproxy.rs"
        bad_emproxy.write_text(pristine_emproxy)
        run(
            sys.executable,
            str(scripts / "patch_v13_emproxy_payload_diag.py"),
            str(bad_emproxy),
            expect_success=False,
        )

        print("v13 local suite PASS")
        print(f"  idempotence_sha256={first_hash}")
        print("  v12 regression suite PASS")
        print("  combined Lockdown frame write + big-endian prefix verified")
        print("  QueryType-first StartSession/CoreDevice/RSD order verified")
        print(f"  serialized_gateway_operations={serial_count}")
        print("  local patched IDevice + bounded payload-length diagnostics verified")
        print("  privacy, ownership, source-drift, and strict live-UDID gates verified")


if __name__ == "__main__":
    main()
