#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_listener_parity.py <remote_pairing/mod.rs>")

p = Path(sys.argv[1])
s = p.read_text()

marker = "[SS-LISTENER] pymobiledevice3 listener parity active"
if marker in s:
    required = [
        '"peerConnectionsInfo"',
        '"owningPID"',
        '"owningProcessName": "CoreDeviceService"',
        "[SS-LISTENER] createListener START",
        "[SS-LISTENER] createListener SUCCESS",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"Listener parity marker present but patch incomplete: {missing}")
    print("Listener parity patch already present and verified")
    raise SystemExit(0)

old = '''    pub async fn create_tcp_listener(&mut self) -> Result<u16, IdeviceError> {
        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                        "transportProtocolType": "tcp"
                    }
                }
            }
        });

        let response = self.send_receive_encrypted_request(request).await?;
        debug!("createListener response: {response:#?}");

        let listener = find_in_plist(&response, "createListener").unwrap_or(&response);

        let port = find_in_plist(listener, "port")
            .or_else(|| find_in_plist(listener, "listenerPort"))
            .and_then(|p| p.as_unsigned_integer())
            .ok_or_else(|| {
                IdeviceError::UnexpectedResponse(format!(
                    "missing createListener.port in response: {response:?}"
                ))
            })?;

        Ok(port as u16)
    }
'''

new = '''    pub async fn create_tcp_listener(&mut self) -> Result<u16, IdeviceError> {
        // pymobiledevice3 sends peerConnectionsInfo for TCP listeners. Recent iOS
        // accepts the legacy request without it, but the resulting socket may close
        // after TLS when the first CDTunnel record is sent. Match CoreDeviceService's
        // request shape exactly while keeping a legacy fallback if the metadata form
        // itself is rejected. Do not log the PSK/key.
        tracing::error!("[SS-LISTENER] pymobiledevice3 listener parity active");
        tracing::error!(
            "[SS-LISTENER] createListener START metadata=peerConnectionsInfo process=CoreDeviceService pid_present=true"
        );

        let peer_info = plist::Value::Array(vec![plist!({
            "owningPID": plist::Value::Integer((std::process::id() as u64).into()),
            "owningProcessName": "CoreDeviceService"
        })]);

        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                        "peerConnectionsInfo": peer_info,
                        "transportProtocolType": "tcp"
                    }
                }
            }
        });

        let response = match self.send_receive_encrypted_request(request).await {
            Ok(response) => response,
            Err(metadata_error) => {
                tracing::error!(
                    "[SS-LISTENER] metadata createListener FAILED; retrying legacy shape error={:?}",
                    metadata_error
                );
                let legacy_request = plist!({
                    "request": {
                        "_0": {
                            "createListener": {
                                "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                                "transportProtocolType": "tcp"
                            }
                        }
                    }
                });
                self.send_receive_encrypted_request(legacy_request).await?
            }
        };
        debug!("createListener response: {response:#?}");

        let listener = find_in_plist(&response, "createListener").unwrap_or(&response);

        let port = find_in_plist(listener, "port")
            .or_else(|| find_in_plist(listener, "listenerPort"))
            .and_then(|p| p.as_unsigned_integer())
            .ok_or_else(|| {
                IdeviceError::UnexpectedResponse(format!(
                    "missing createListener.port in response: {response:?}"
                ))
            })?;

        tracing::error!(
            "[SS-LISTENER] createListener SUCCESS port={} metadata_attempted=true",
            port
        );
        Ok(port as u16)
    }
'''

if old not in s:
    raise SystemExit("Could not locate exact create_tcp_listener implementation")

s = s.replace(old, new, 1)
p.write_text(s)

patched = p.read_text()
required = [
    marker,
    '"peerConnectionsInfo"',
    '"owningPID"',
    '"owningProcessName": "CoreDeviceService"',
    "std::process::id()",
    "[SS-LISTENER] createListener START",
    "[SS-LISTENER] createListener SUCCESS",
    "retrying legacy shape",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"Listener parity verification failed: {missing}")

print("pymobiledevice3 TCP listener metadata parity applied and verified")
