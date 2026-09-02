#!/usr/bin/env python3
"""Serialize V23 tunnel creation and force native error-level diagnostics on."""
from __future__ import annotations
from pathlib import Path
import sys

MARK = "[SS-V23-RPDIAG]"

def die(msg): raise SystemExit(msg)
def once(text, old, new, label):
    n=text.count(old)
    if n != 1: die(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old,new,1)

def verify(path: Path):
    s=path.read_text()
    req=[
        "private let v23TunnelCreationLock = NSRecursiveLock()",
        "TUNNEL_LOCK_WAIT",
        "TUNNEL_LOCK_ACQUIRED",
        "TUNNEL_LOCK_RELEASE",
        "native IDevice error-level diagnostics forced ON",
    ]
    miss=[x for x in req if x not in s]
    if miss: die(f"missing v23 gateway diagnostics: {miss}")

def main():
    if len(sys.argv)!=2: die("usage: patch_v23_gateway_diag.py <minimuxer-root>")
    root=Path(sys.argv[1])
    p=root/'DeviceGateway/idevice/IdeviceGateway.swift'
    if not p.is_file(): die(f"missing {p}")
    s=p.read_text()
    if "private let v23TunnelCreationLock = NSRecursiveLock()" in s:
        verify(p); print("v23 gateway diagnostics already present and verified"); return

    state='''    private var activeTunnelTransport: String? = nil\n    private var adapter: OpaquePointer? = nil\n'''
    state_new='''    private var activeTunnelTransport: String? = nil\n    private let v23TunnelCreationLock = NSRecursiveLock()\n    private var adapter: OpaquePointer? = nil\n'''
    s=once(s,state,state_new,'v23 tunnel lock state')

    logging='''        idevice_init_logger(enabled ? IdeviceLogLevel(rawValue: 1) : IdeviceLogLevel(rawValue: 0), IdeviceLogLevel(rawValue: 0), nil)\n'''
    logging_new='''        // V23 native diagnostics use tracing::error! markers. Keep error-level\n        // IDevice logging enabled even when normal verbose logging is disabled.\n        idevice_init_logger(IdeviceLogLevel(rawValue: 1), IdeviceLogLevel(rawValue: 0), nil)\n        debugLog("[SS-V23-RPDIAG] native IDevice error-level diagnostics forced ON")\n'''
    s=once(s,logging,logging_new,'force native diagnostic logger')

    func='''    private func ensureAdaptiveConnection() throws {\n        if adapter != nil && handshake != nil { return }\n'''
    func_new='''    private func ensureAdaptiveConnection() throws {\n        debugLog("[SS-V23-RPDIAG] TUNNEL_LOCK_WAIT main=\\(Thread.isMainThread)")\n        v23TunnelCreationLock.lock()\n        debugLog("[SS-V23-RPDIAG] TUNNEL_LOCK_ACQUIRED main=\\(Thread.isMainThread)")\n        defer {\n            debugLog("[SS-V23-RPDIAG] TUNNEL_LOCK_RELEASE main=\\(Thread.isMainThread)")\n            v23TunnelCreationLock.unlock()\n        }\n        if adapter != nil && handshake != nil { return }\n'''
    s=once(s,func,func_new,'serialize adaptive tunnel creation')
    p.write_text(s)
    verify(p)
    print("v23 gateway serialization + native diagnostic logger applied and verified")

if __name__=='__main__': main()
