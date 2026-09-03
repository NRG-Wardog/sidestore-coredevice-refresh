#!/usr/bin/env python3
"""Patch SideStore/idevice to support native on-device QUIC listener creation."""
from pathlib import Path
import sys

def die(msg: str):
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)

def once(text: str, anchor: str, replacement: str, label: str) -> str:
    n = text.count(anchor)
    if n != 1:
        die(f"{label}: expected 1 match for anchor, found {n}")
    return text.replace(anchor, replacement, 1)

def patch_remote_pairing_mod(root: Path) -> None:
    path = root / "idevice" / "src" / "remote_pairing" / "mod.rs"
    if not path.exists():
        die(f"remote_pairing/mod.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if "pub async fn create_quic_listener" in text:
        print("remote_pairing/mod.rs already has create_quic_listener")
        return

    anchor = "    pub async fn create_tcp_listener(&mut self) -> Result<u16, IdeviceError> {"
    if anchor not in text:
        die("Could not find create_tcp_listener anchor in remote_pairing/mod.rs")

    quic_method = '''    /// Autonomous connect that establishes encryption keys
    pub async fn connect_any(&mut self) -> Result<(), IdeviceError> {
        let _ = self.attempt_pair_verify().await;
        let mut rpf = RpPairingFile::generate(&self.sending_host);
        let _ = self.validate_pairing(&mut rpf).await;
        let (cc, sc) = Self::derive_main_ciphers(&self.encryption_key);
        self.client_cipher = cc;
        self.server_cipher = sc;
        Ok(())
    }

    /// Send a request to create a QUIC tunnel listener on the device.
    /// Returns the dynamic UDP port the device is listening on.
    pub async fn create_quic_listener(&mut self, client_pub_b64: &str) -> Result<u16, IdeviceError> {
        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": client_pub_b64,
                        "peerConnectionsInfo": [
                            {
                                "owningPID": std::process::id() as u64,
                                "owningProcessName": "CoreDeviceService"
                            }
                        ],
                        "transportProtocolType": "quic"
                    }
                }
            }
        });

        let response = self.send_receive_encrypted_request(request).await?;
        tracing::error!("[RP-QUIC] createListener(quic) response: {response:#?}");

        let listener = find_in_plist(&response, "createListener").unwrap_or(&response);

        let port = find_in_plist(listener, "port")
            .or_else(|| find_in_plist(listener, "listenerPort"))
            .and_then(|p| p.as_unsigned_integer())
            .ok_or(IdeviceError::UnexpectedResponse(
                "missing port in createListener(quic) response".into(),
            ))?;

        Ok(port as u16)
    }

'''
    text = once(text, anchor, quic_method + anchor, "create_quic_listener method")
    path.write_text(text, encoding="utf-8")
    print("Successfully patched idevice/src/remote_pairing/mod.rs with connect_any and create_quic_listener")

def patch_tunnel_provider(root: Path) -> None:
    path = root / "ffi" / "src" / "tunnel_provider.rs"
    if not path.exists():
        die(f"tunnel_provider.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if "rppairing_create_quic_listener" in text:
        print("tunnel_provider.rs already has rppairing_create_quic_listener")
        return

    ffi_function = '''
/// Creates a dynamic QUIC listener on the remote device via RemotePairing control channel.
/// Returns the dynamic UDP port for native QUIC connection.
///
/// # Safety
/// All pointer arguments must be valid and non-null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rppairing_create_quic_listener(
    addr: *const idevice_sockaddr,
    addr_len: idevice_socklen_t,
    hostname: *const c_char,
    client_pub_b64: *const c_char,
    out_port: *mut u16,
) -> *mut IdeviceFfiError {
    if addr.is_null()
        || hostname.is_null()
        || client_pub_b64.is_null()
        || out_port.is_null()
    {
        return ffi_err!(IdeviceError::FfiInvalidArg);
    }

    let socket_addr = match crate::util::c_socket_to_rust(addr as *const SockAddr, addr_len) {
        Ok(a) => a,
        Err(e) => return ffi_err!(e),
    };
    let host = match unsafe { CStr::from_ptr(hostname) }.to_str() {
        Ok(s) => s.to_string(),
        Err(_) => return ffi_err!(IdeviceError::FfiInvalidString),
    };
    let client_pub = match unsafe { CStr::from_ptr(client_pub_b64) }.to_str() {
        Ok(s) => s.to_string(),
        Err(_) => return ffi_err!(IdeviceError::FfiInvalidString),
    };

    let res = run_sync_local(async {
        let stream = run_global_timeout(|| tokio::net::TcpStream::connect(socket_addr))
            .await
            .map_err(|e| IdeviceError::InternalError(format!("connect: {e}")))?;
        let conn = RpPairingSocket::new(stream);

        let mut rpc = RemotePairingClient::new(conn, &host);
        rpc.connect_any().await?;

        rpc.create_quic_listener(&client_pub).await
    });

    match res {
        Ok(port) => {
            unsafe { *out_port = port; }
            null_mut()
        }
        Err(e) => ffi_err!(e),
    }
}
'''
    text += ffi_function
    path.write_text(text, encoding="utf-8")
    print("Successfully patched ffi/src/tunnel_provider.rs with rppairing_create_quic_listener")

def patch_idevice_h(root: Path) -> None:
    path = root / "ffi" / "idevice.h"
    if not path.exists():
        print("ffi/idevice.h does not exist before build (will be generated by cbindgen), skipping")
        return
    text = path.read_text(encoding="utf-8")
    if "rppairing_create_quic_listener" in text:
        print("ffi/idevice.h already has rppairing_create_quic_listener")
        return

    sig = '''
/**
 * Creates a dynamic QUIC listener on the remote device via RemotePairing control channel.
 * Returns the dynamic UDP port for native QUIC connection.
 */
struct IdeviceFfiError *rppairing_create_quic_listener(const idevice_sockaddr *addr,
                                                       idevice_socklen_t addr_len,
                                                       const char *hostname,
                                                       const char *client_pub_b64,
                                                       uint16_t *out_port);
'''
    text += sig
    path.write_text(text, encoding="utf-8")
    print("Successfully patched ffi/idevice.h with rppairing_create_quic_listener declaration")

def main():
    if len(sys.argv) < 2:
        die("Usage: patch_v28_quic_coredevice.py <idevice-repo-root>")
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        die(f"Directory does not exist: {root}")
    patch_remote_pairing_mod(root)
    patch_tunnel_provider(root)
    patch_idevice_h(root)
    print("ALL V28 QUIC COREDEVICE PATCHES APPLIED SUCCESSFULLY!")

if __name__ == '__main__':
    main()
