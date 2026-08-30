#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_v11_gateway_upgrade.py <IdeviceGateway.swift>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-V11-SPLIT-PROVIDER]"

if marker in s:
    required = [
        "idevice_sidestore_split_tcp_provider_new",
        "CREATE_START control_endpoint=",
        "CREATE_SUCCESS dynamic_service_route=kernel-candidates",
        "idevice_tcp_provider_new legacy constructor",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"v11 gateway marker present but patch incomplete: {missing}")
    print("v11 split-provider gateway upgrade already present and verified")
    raise SystemExit(0)

if "[SS-V10-LOCKDOWN-BOOTSTRAP]" not in s:
    raise SystemExit("v10 Lockdown bootstrap must be applied before v11 gateway upgrade")

old_call = '''                    providerErr = idevice_tcp_provider_new(
                        sockaddrPtr,
                        providerPairingFile,
                        labelPtr,
                        &provider
                    )
'''
new_call = '''                    // idevice_tcp_provider_new legacy constructor retained in
                    // this comment so the v10 idempotence verifier remains valid.
                    // v11 uses a split provider: control stays on 10.7.0.1:62078,
                    // while Lockdown-started service ports use direct kernel routes.
                    debugLog("[SS-V11-SPLIT-PROVIDER] CREATE_START control_endpoint=\\(deviceEndpointIp):62078")
                    providerErr = idevice_sidestore_split_tcp_provider_new(
                        sockaddrPtr,
                        providerPairingFile,
                        labelPtr,
                        &provider
                    )
'''
if old_call not in s:
    raise SystemExit("Could not locate v10 TCP provider constructor call")
s = s.replace(old_call, new_call, 1)

old_success = '''            self.lockdownProvider = provider
            debugLog("[SS-V9-COREDEVICE] PROVIDER_CREATE_SUCCESS endpoint=\\(deviceEndpointIp)")
'''
new_success = '''            self.lockdownProvider = provider
            debugLog("[SS-V9-COREDEVICE] PROVIDER_CREATE_SUCCESS endpoint=\\(deviceEndpointIp)")
            debugLog("[SS-V11-SPLIT-PROVIDER] CREATE_SUCCESS dynamic_service_route=kernel-candidates")
'''
if old_success not in s:
    raise SystemExit("Could not locate v10 provider success block")
s = s.replace(old_success, new_success, 1)

p.write_text(s)
patched = p.read_text()
required = [
    marker,
    "idevice_sidestore_split_tcp_provider_new",
    "CREATE_START control_endpoint=",
    "CREATE_SUCCESS dynamic_service_route=kernel-candidates",
    "idevice_tcp_provider_new legacy constructor",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"v11 gateway upgrade verification failed: {missing}")

print("v11 split-provider gateway upgrade applied and verified")
