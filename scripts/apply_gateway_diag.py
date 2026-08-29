#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_gateway_diag.py <IdeviceGateway.swift>")

p = Path(sys.argv[1])
s = p.read_text()

if "[GW-DIAG] fetchUDID lockdownd_connect_rsd START" in s:
    print("DeviceGateway diagnostic patch already present")
    raise SystemExit(0)

old = '''        debugLog("[IdeviceGateway] ensureRPConnection() tunnel_create_rppairing succeeded, adapter: \\(String(describing: adapter)), handshake: \\(String(describing: handshake))")'''
new = '''        debugLog("[IdeviceGateway] ensureRPConnection() tunnel_create_rppairing succeeded, adapter: \\(String(describing: adapter)), handshake: \\(String(describing: handshake))")
        debugLog("[GW-DIAG] tunnel ready endpoint=\\(deviceEndpointIp):\\(remotePairingPort) adapterPresent=\\(adapter != nil) handshakePresent=\\(handshake != nil)")'''
if old not in s:
    raise SystemExit("Could not locate ensureRPConnection success log")
s = s.replace(old, new, 1)

old = '''        var client: OpaquePointer? = nil
        var err = connect(adapter, handshake, &client)
        if let firstErr = err {'''
new = '''        var client: OpaquePointer? = nil
        debugLog("[GW-DIAG] service connect START name=\\(serviceName) adapterPresent=\\(adapter != nil) handshakePresent=\\(handshake != nil)")
        var err = connect(adapter, handshake, &client)
        if err == nil {
            debugLog("[GW-DIAG] service connect SUCCESS name=\\(serviceName) clientPresent=\\(client != nil)")
        }
        if let firstErr = err {'''
if old not in s:
    raise SystemExit("Could not locate performWithService initial connect")
s = s.replace(old, new, 1)

old = '''                try ensureRPConnection()
                err = connect(adapter, handshake, &client)
            } catch {'''
new = '''                try ensureRPConnection()
                debugLog("[GW-DIAG] service retry connect START name=\\(serviceName)")
                err = connect(adapter, handshake, &client)
                if err == nil {
                    debugLog("[GW-DIAG] service retry connect SUCCESS name=\\(serviceName) clientPresent=\\(client != nil)")
                }
            } catch {'''
if old not in s:
    raise SystemExit("Could not locate performWithService retry connect")
s = s.replace(old, new, 1)

old = '''            var lockdownClient: OpaquePointer? = nil
            verboseLog("[IdeviceGateway] fetchUDID() connecting lockdownd_connect_rsd")
            var connectErr = lockdownd_connect_rsd(adapter, handshake, &lockdownClient)
            if let firstErr = connectErr {
                debugLog("[IdeviceGateway] fetchUDID() lockdownd_connect_rsd failed on existing connection, invalidating and retrying with fresh connection")
                idevice_error_free(firstErr)
                invalidateConnection()'''
new = '''            var lockdownClient: OpaquePointer? = nil
            verboseLog("[IdeviceGateway] fetchUDID() connecting lockdownd_connect_rsd")
            debugLog("[GW-DIAG] fetchUDID lockdownd_connect_rsd START")
            var connectErr = lockdownd_connect_rsd(adapter, handshake, &lockdownClient)
            if connectErr == nil {
                debugLog("[GW-DIAG] fetchUDID lockdownd_connect_rsd SUCCESS clientPresent=\\(lockdownClient != nil)")
            }
            if let firstErr = connectErr {
                let firstCode = firstErr.pointee.code
                let firstSubCode = firstErr.pointee.sub_code
                let firstMsg = getErrorMessage(from: firstErr)
                debugLog("[GW-DIAG] fetchUDID lockdownd_connect_rsd FAILED code=\\(firstCode) subCode=\\(firstSubCode) message=\\(firstMsg)")
                debugLog("[IdeviceGateway] fetchUDID() lockdownd_connect_rsd failed on existing connection, invalidating and retrying with fresh connection")
                idevice_error_free(firstErr)
                invalidateConnection()'''
if old not in s:
    raise SystemExit("Could not locate fetchUDID initial lockdownd connect")
s = s.replace(old, new, 1)

old = '''                    connectErr = lockdownd_connect_rsd(freshAdapter, freshHandshake, &lockdownClient)
                    if let secondErr = connectErr {
                        debugLog("[IdeviceGateway] fetchUDID() lockdownd_connect_rsd retry failed")
                        idevice_error_free(secondErr)'''
new = '''                    debugLog("[GW-DIAG] fetchUDID lockdownd_connect_rsd RETRY START")
                    connectErr = lockdownd_connect_rsd(freshAdapter, freshHandshake, &lockdownClient)
                    if connectErr == nil {
                        debugLog("[GW-DIAG] fetchUDID lockdownd_connect_rsd RETRY SUCCESS clientPresent=\\(lockdownClient != nil)")
                    }
                    if let secondErr = connectErr {
                        let secondCode = secondErr.pointee.code
                        let secondSubCode = secondErr.pointee.sub_code
                        let secondMsg = getErrorMessage(from: secondErr)
                        debugLog("[GW-DIAG] fetchUDID lockdownd_connect_rsd RETRY FAILED code=\\(secondCode) subCode=\\(secondSubCode) message=\\(secondMsg)")
                        debugLog("[IdeviceGateway] fetchUDID() lockdownd_connect_rsd retry failed")
                        idevice_error_free(secondErr)'''
if old not in s:
    raise SystemExit("Could not locate fetchUDID retry lockdownd connect")
s = s.replace(old, new, 1)

old = '''            defer { lockdownd_client_free(client) }
            
            var plistVal: plist_t? = nil
            verboseLog("[IdeviceGateway] fetchUDID() calling lockdownd_get_value for UniqueDeviceID")
            let valErr = lockdownd_get_value(client, "UniqueDeviceID", nil, &plistVal)
            if let valErr = valErr {
                debugLog("[IdeviceGateway] fetchUDID() lockdownd_get_value failed")
                safeFreeError(valErr)
                return nil
            }'''
new = '''            defer { lockdownd_client_free(client) }
            debugLog("[GW-DIAG] fetchUDID lockdown client READY")
            
            var plistVal: plist_t? = nil
            verboseLog("[IdeviceGateway] fetchUDID() calling lockdownd_get_value for UniqueDeviceID")
            debugLog("[GW-DIAG] fetchUDID lockdownd_get_value START key=UniqueDeviceID")
            let valErr = lockdownd_get_value(client, "UniqueDeviceID", nil, &plistVal)
            if let valErr = valErr {
                let valCode = valErr.pointee.code
                let valSubCode = valErr.pointee.sub_code
                let valMsg = getErrorMessage(from: valErr)
                debugLog("[GW-DIAG] fetchUDID lockdownd_get_value FAILED code=\\(valCode) subCode=\\(valSubCode) message=\\(valMsg)")
                debugLog("[IdeviceGateway] fetchUDID() lockdownd_get_value failed")
                safeFreeError(valErr)
                return nil
            }
            debugLog("[GW-DIAG] fetchUDID lockdownd_get_value SUCCESS plistPresent=\\(plistVal != nil)")'''
if old not in s:
    raise SystemExit("Could not locate fetchUDID lockdownd_get_value block")
s = s.replace(old, new, 1)

old = '''                let udid = getRustPlistString(plistVal)
                verboseLog("[IdeviceGateway] fetchUDID() getRustPlistString returned UDID: \\(String(describing: udid))")
                return udid'''
new = '''                let udid = getRustPlistString(plistVal)
                verboseLog("[IdeviceGateway] fetchUDID() getRustPlistString returned UDID: \\(String(describing: udid))")
                debugLog("[GW-DIAG] fetchUDID parse result present=\\(udid != nil) length=\\(udid?.count ?? 0)")
                return udid'''
if old not in s:
    raise SystemExit("Could not locate fetchUDID parse result")
s = s.replace(old, new, 1)

p.write_text(s)

required = [
    "[GW-DIAG] tunnel ready",
    "[GW-DIAG] service connect START",
    "[GW-DIAG] service retry connect",
    "[GW-DIAG] fetchUDID lockdownd_connect_rsd START",
    "[GW-DIAG] fetchUDID lockdownd_get_value START",
    "[GW-DIAG] fetchUDID parse result",
]
patched = p.read_text()
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"Patch verification failed; missing: {missing}")

print("DeviceGateway downstream diagnostic patch applied and verified")
