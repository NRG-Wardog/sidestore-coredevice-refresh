#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_gateway_serialization.py <IdeviceGateway.swift>")

p = Path(sys.argv[1])
s = p.read_text()


def apply_v9_coredevice() -> None:
    script = Path(__file__).with_name("apply_coredevice_lockdown.py")
    if not script.exists():
        raise SystemExit(f"Missing required v9 CoreDevice patch script: {script}")
    subprocess.check_call([sys.executable, str(script), str(p)])
    patched = p.read_text()
    required = [
        "[SS-V9-COREDEVICE]",
        "RP_FILE_CANNOT_USE_COREDEVICE_PROXY",
        "idevice_tcp_provider_new",
        "tunnel_create_usb",
        "COREDEVICE_TUNNEL_SUCCESS",
    ]
    missing = [x for x in required if x not in patched]
    if missing:
        raise SystemExit(f"v9 CoreDevice integration verification failed; missing: {missing}")


marker = "[GW-SERIAL]"
if marker in s:
    required = [
        "private let connectionOperationLock = NSRecursiveLock()",
        "[GW-SERIAL] performWithService lock acquired",
        "[GW-SERIAL] fetchUDID lock acquired",
        "[GW-SERIAL] ensureRPConnection lock acquired",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"Gateway serialization marker present but incomplete; missing: {missing}")
    apply_v9_coredevice()
    print("Gateway serialization + v9 CoreDeviceProxy patches already present and verified")
    raise SystemExit(0)

anchor = '''    private var remotePairingPort: UInt16 = MinimuxerConstants.remotePairingPort
    private var isInitialized = false'''
replacement = '''    private var remotePairingPort: UInt16 = MinimuxerConstants.remotePairingPort
    // All RPPairing tunnel creation/service use shares mutable adapter, handshake,
    // and pairing state. Startup DDI checks, fetchUDID, and Refresh can overlap,
    // so serialize these operations to prevent concurrent tunnel creation or a
    // connection being invalidated while another service is using it.
    private let connectionOperationLock = NSRecursiveLock()
    private var isInitialized = false'''
if anchor not in s:
    raise SystemExit("Could not locate gateway state anchor")
s = s.replace(anchor, replacement, 1)

old = '''    public func setRemotePairingPort(_ port: UInt16) {
        debugLog("[IdeviceGateway] setRemotePairingPort(\\(port)) called")'''
new = '''    public func setRemotePairingPort(_ port: UInt16) {
        connectionOperationLock.lock()
        defer { connectionOperationLock.unlock() }
        debugLog("[GW-SERIAL] setRemotePairingPort lock acquired")
        debugLog("[IdeviceGateway] setRemotePairingPort(\\(port)) called")'''
if old not in s:
    raise SystemExit("Could not locate setRemotePairingPort")
s = s.replace(old, new, 1)

old = '''    private func cleanup() {
        debugLog("[IdeviceGateway] cleanup() called")'''
new = '''    private func cleanup() {
        connectionOperationLock.lock()
        defer { connectionOperationLock.unlock() }
        debugLog("[GW-SERIAL] cleanup lock acquired")
        debugLog("[IdeviceGateway] cleanup() called")'''
if old not in s:
    raise SystemExit("Could not locate cleanup")
s = s.replace(old, new, 1)

old = '''    private func invalidateConnection() {
        debugLog("[IdeviceGateway] invalidateConnection() called - clearing stale adapter and handshake")'''
new = '''    private func invalidateConnection() {
        connectionOperationLock.lock()
        defer { connectionOperationLock.unlock() }
        debugLog("[GW-SERIAL] invalidateConnection lock acquired")
        debugLog("[IdeviceGateway] invalidateConnection() called - clearing stale adapter and handshake")'''
if old not in s:
    raise SystemExit("Could not locate invalidateConnection")
s = s.replace(old, new, 1)

old = '''    public func setDeviceEndpointIp(_ ip: String?) {
        debugLog("[IdeviceGateway] setDeviceEndpointIp(\\(ip ?? \"nil\")) called")'''
new = '''    public func setDeviceEndpointIp(_ ip: String?) {
        connectionOperationLock.lock()
        defer { connectionOperationLock.unlock() }
        debugLog("[GW-SERIAL] setDeviceEndpointIp lock acquired")
        debugLog("[IdeviceGateway] setDeviceEndpointIp(\\(ip ?? \"nil\")) called")'''
if old not in s:
    raise SystemExit("Could not locate setDeviceEndpointIp")
s = s.replace(old, new, 1)

old = '''    private func syncStart(pairingFileContent: String) throws {
        debugLog("[IdeviceGateway] start() called, pairingFileContent length: \\(pairingFileContent.count)")'''
new = '''    private func syncStart(pairingFileContent: String) throws {
        connectionOperationLock.lock()
        defer { connectionOperationLock.unlock() }
        debugLog("[GW-SERIAL] start lock acquired")
        debugLog("[IdeviceGateway] start() called, pairingFileContent length: \\(pairingFileContent.count)")'''
if old not in s:
    raise SystemExit("Could not locate syncStart")
s = s.replace(old, new, 1)

old = '''    private func ensureRPConnection() throws {
        debugLog("[IdeviceGateway] ensureRPConnection() started, adapter: \\(String(describing: adapter)), handshake: \\(String(describing: handshake))")'''
new = '''    private func ensureRPConnection() throws {
        connectionOperationLock.lock()
        defer { connectionOperationLock.unlock() }
        debugLog("[GW-SERIAL] ensureRPConnection lock acquired")
        debugLog("[IdeviceGateway] ensureRPConnection() started, adapter: \\(String(describing: adapter)), handshake: \\(String(describing: handshake))")'''
if old not in s:
    raise SystemExit("Could not locate ensureRPConnection")
s = s.replace(old, new, 1)

old = '''    private func performWithService<T>(
        connect: @escaping (OpaquePointer?, OpaquePointer?, UnsafeMutablePointer<OpaquePointer?>?) -> UnsafeMutablePointer<IdeviceFfiError>?,
        cleanup: @escaping (OpaquePointer?) -> Void,
        serviceName: String,
        action: (OpaquePointer) throws -> T
    ) throws -> T {
        debugLog("[IdeviceGateway] performWithService(\\(serviceName)) started")'''
new = '''    private func performWithService<T>(
        connect: @escaping (OpaquePointer?, OpaquePointer?, UnsafeMutablePointer<OpaquePointer?>?) -> UnsafeMutablePointer<IdeviceFfiError>?,
        cleanup: @escaping (OpaquePointer?) -> Void,
        serviceName: String,
        action: (OpaquePointer) throws -> T
    ) throws -> T {
        connectionOperationLock.lock()
        defer { connectionOperationLock.unlock() }
        debugLog("[GW-SERIAL] performWithService lock acquired name=\\(serviceName)")
        debugLog("[IdeviceGateway] performWithService(\\(serviceName)) started")'''
if old not in s:
    raise SystemExit("Could not locate performWithService")
s = s.replace(old, new, 1)

old = '''    private func syncFetchUDID() throws -> String? {
        debugLog("[IdeviceGateway] fetchUDID() started, isRPPairing: \\(isRPPairing) (mode = .\\(pairingFileType))")'''
new = '''    private func syncFetchUDID() throws -> String? {
        connectionOperationLock.lock()
        defer { connectionOperationLock.unlock() }
        debugLog("[GW-SERIAL] fetchUDID lock acquired")
        debugLog("[IdeviceGateway] fetchUDID() started, isRPPairing: \\(isRPPairing) (mode = .\\(pairingFileType))")'''
if old not in s:
    raise SystemExit("Could not locate syncFetchUDID")
s = s.replace(old, new, 1)

p.write_text(s)

required = [
    "private let connectionOperationLock = NSRecursiveLock()",
    "[GW-SERIAL] setRemotePairingPort lock acquired",
    "[GW-SERIAL] cleanup lock acquired",
    "[GW-SERIAL] invalidateConnection lock acquired",
    "[GW-SERIAL] setDeviceEndpointIp lock acquired",
    "[GW-SERIAL] start lock acquired",
    "[GW-SERIAL] ensureRPConnection lock acquired",
    "[GW-SERIAL] performWithService lock acquired",
    "[GW-SERIAL] fetchUDID lock acquired",
]
patched = p.read_text()
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"Gateway serialization verification failed; missing: {missing}")

apply_v9_coredevice()
print("Gateway serialization + v9 CoreDeviceProxy lockdown transport applied and verified")
