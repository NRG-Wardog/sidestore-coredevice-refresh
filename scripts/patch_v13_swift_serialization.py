#!/usr/bin/env python3
"""Serialize IdeviceGateway FFI operations and retain native v13 diagnostics."""

from __future__ import annotations

from pathlib import Path
import sys

MARKER = "[SS-V13-HYBRID]"


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def verify(text: str) -> None:
    required = [
        MARKER,
        'DispatchQueue(label: "com.SideStore.IdeviceGateway.v13.serial"',
        "withFFIDispatch(on: Self.v13FFIQueue)",
        "native IDevice error diagnostics active",
        "idevice_init_logger(IdeviceLogLevel(rawValue: 1)",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        die(f"v13 Swift serialization verification failed; missing: {missing}")

    if "withFFIDispatch {" in text:
        die("v13 Swift serialization verification failed: a default concurrent FFI dispatch remains")
    count = text.count("withFFIDispatch(on: Self.v13FFIQueue)")
    if count < 10:
        die(f"v13 Swift serialization verification failed: expected all gateway operations on serial queue, found {count}")
    if "enabled ? IdeviceLogLevel(rawValue: 1) : IdeviceLogLevel(rawValue: 0)" in text:
        die("v13 Swift serialization verification failed: native error layer can still be disabled")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v13_swift_serialization.py <IdeviceGateway.swift>")

    path = Path(sys.argv[1])
    if not path.exists():
        die(f"missing IdeviceGateway.swift: {path}")
    source = path.read_text()

    if MARKER in source:
        verify(source)
        print("v13 Swift FFI serialization patch already present and verified")
        return

    shared_anchor = "    public static let shared = IdeviceGateway()\n"
    shared_replacement = '''    public static let shared = IdeviceGateway()
    // v13: all blocking native gateway operations share one queue.  The v12
    // runtime log showed DDI probing, boot validation, and refresh creating
    // overlapping Lockdown and RemotePairing sessions on the default global
    // concurrent queue.
    private static let v13FFIQueue = DispatchQueue(
        label: "com.SideStore.IdeviceGateway.v13.serial",
        qos: .userInitiated
    )
'''
    if source.count(shared_anchor) != 1:
        die(f"shared instance anchor: expected once, found {source.count(shared_anchor)}")
    source = source.replace(shared_anchor, shared_replacement, 1)

    old_logger = "        idevice_init_logger(enabled ? IdeviceLogLevel(rawValue: 1) : IdeviceLogLevel(rawValue: 0), IdeviceLogLevel(rawValue: 0), nil)\n"
    new_logger = '''        // Keep the native ERROR layer active in release builds.  v13 native
        // markers are secret-free and are required to identify the exact
        // Lockdown/CoreDevice stage without enabling verbose packet contents.
        idevice_init_logger(IdeviceLogLevel(rawValue: 1), IdeviceLogLevel(rawValue: 0), nil)
        debugLog("[SS-V13-HYBRID] native IDevice error diagnostics active; user_verbose=\\(enabled)")
'''
    if source.count(old_logger) != 1:
        die(f"native logger anchor: expected once, found {source.count(old_logger)}")
    source = source.replace(old_logger, new_logger, 1)

    dispatch_count = source.count("withFFIDispatch {")
    if dispatch_count < 10:
        die(f"FFI dispatch anchor drift: expected at least 10 operations, found {dispatch_count}")
    source = source.replace(
        "withFFIDispatch {",
        "withFFIDispatch(on: Self.v13FFIQueue) {",
    )

    path.write_text(source)
    verify(path.read_text())
    print(f"v13 serialized {dispatch_count} IdeviceGateway FFI operations and enabled native error diagnostics")


if __name__ == "__main__":
    main()
