#!/usr/bin/env python3
"""Force minimuxer onto the v12 IDevice hybrid gateway and fix cache switching."""

from pathlib import Path
import sys

MARKER = "[SS-V12-HYBRID]"


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def verify(text: str) -> None:
    required = [
        MARKER,
        "currentBackend: GatewayBackend = .idevice",
        "let previousBackend = currentBackend",
        "let requestedBackend = backend ?? .idevice",
        "let resolvedBackend: GatewayBackend = .idevice",
        "previousBackend == resolvedBackend",
        "IdeviceGateway.shared.setRemotePairingPort(resolvedPort)",
        "creating fresh Minimuxer instance backend=",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        die(f"v12 backend verification failed: {missing}")
    forbidden = [
        "let resolvedBackend = backend ?? currentBackend",
        "if let cached = cachedInstance, currentBackend == resolvedBackend",
        "let resolvedBackend: GatewayBackend = .libimobiledevice",
    ]
    leaked = [item for item in forbidden if item in text]
    if leaked:
        die(f"v12 backend verification failed; stale backend code remains: {leaked}")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v12_hybrid_backend.py <Sources/MinimuxerApi.swift>")
    path = Path(sys.argv[1])
    text = path.read_text()
    if MARKER in text:
        verify(text)
        print("v12 hybrid backend patch already present and verified")
        return

    old = '''        let resolvedBackend = backend ?? currentBackend
        let resolvedPort = remotePairingPort ?? currentRemotePairingPort

        currentBackend = resolvedBackend
        currentRemotePairingPort = resolvedPort

        switch resolvedBackend {
        case .libimobiledevice:
            LibimobiledeviceGateway.shared.setRemotePairingPort(resolvedPort)
        case .idevice:
            IdeviceGateway.shared.setRemotePairingPort(resolvedPort)
        }

        if let cached = cachedInstance, currentBackend == resolvedBackend {
            return cached
        }
        return createInstance(backend: resolvedBackend)
'''
    new = '''        let previousBackend = currentBackend
        let requestedBackend = backend ?? .idevice
        let resolvedBackend: GatewayBackend = .idevice
        let resolvedPort = remotePairingPort ?? currentRemotePairingPort

        if requestedBackend != .idevice {
            debugLog("[SS-V12-HYBRID] overriding requested backend=\\(requestedBackend.rawValue) with idevice")
        }

        currentBackend = resolvedBackend
        currentRemotePairingPort = resolvedPort
        IdeviceGateway.shared.setRemotePairingPort(resolvedPort)
        debugLog(
            "[SS-V12-HYBRID] shared backend=\\(resolvedBackend.rawValue) " +
            "previous=\\(previousBackend.rawValue) port=\\(resolvedPort)"
        )

        // Compare against the captured previous backend. Upstream assigns
        // currentBackend before comparing it, which makes the cache check
        // tautological and can return a facade wired to the wrong gateway.
        if let cached = cachedInstance, previousBackend == resolvedBackend {
            return cached
        }
        debugLog("[SS-V12-HYBRID] creating fresh Minimuxer instance backend=\\(resolvedBackend.rawValue)")
        return createInstance(backend: resolvedBackend)
'''
    count = text.count(old)
    if count != 1:
        die(f"Could not locate unique Minimuxer.shared block; found {count}")
    text = text.replace(old, new, 1)
    path.write_text(text)
    verify(path.read_text())
    print("v12 IDevice backend enforcement + cache lifecycle fix applied and verified")


if __name__ == "__main__":
    main()
