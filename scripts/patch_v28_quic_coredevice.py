#!/usr/bin/env python3
"""Patch SideStore/idevice to support native on-device QUIC tunnel via Quinn + Rustls."""
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

    quic_method = '''    /// Consumes the client and returns the underlying socket provider.
    pub fn into_inner(self) -> R {
        self.inner
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
        tracing::debug!("[RP-QUIC] createListener(quic) response: {response:#?}");

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
    print("Successfully patched idevice/src/remote_pairing/mod.rs with into_inner and create_quic_listener")

def patch_ffi_cargo_toml(root: Path) -> None:
    path = root / "ffi" / "Cargo.toml"
    if not path.exists():
        die(f"ffi/Cargo.toml not found at {path}")
    text = path.read_text(encoding="utf-8")
    if 'quinn =' in text:
        print("ffi/Cargo.toml already has quinn dependency")
        return

    dep_anchor = "[dependencies]\n"
    if dep_anchor not in text:
        die("Could not find [dependencies] in ffi/Cargo.toml")

    dep = '''quinn = { version = "0.11.11", default-features = false, features = ["rustls-aws-lc-rs", "runtime-tokio"] }
rustls = { version = "0.23", default-features = false, features = ["aws-lc-rs"] }
tinyvec = "=1.8.1"
'''
    text = once(text, dep_anchor, dep_anchor + dep, "add quinn, rustls, tinyvec dependencies")

    default_anchor = 'default = [\n'
    if default_anchor not in text:
        die("Could not find default = [ in ffi/Cargo.toml")
    text = once(text, default_anchor, default_anchor + '  "dep:serde_json",\n', "add dep:serde_json to default features")

    path.write_text(text, encoding="utf-8")
    print("Successfully patched ffi/Cargo.toml with quinn, rustls, tinyvec, and activated serde_json")

def patch_tunnel_provider(root: Path) -> None:
    path = root / "ffi" / "src" / "tunnel_provider.rs"
    if not path.exists():
        die(f"tunnel_provider.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if "rppairing_connect_quic_tunnel" in text:
        print("tunnel_provider.rs already has rppairing_connect_quic_tunnel")
        return

    ffi_functions = '''
#[derive(Debug)]
struct SkipServerVerification;

impl rustls::client::danger::ServerCertVerifier for SkipServerVerification {
    fn verify_server_cert(
        &self,
        _end_entity: &rustls::pki_types::CertificateDer<'_>,
        _intermediates: &[rustls::pki_types::CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp_response: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        vec![
            rustls::SignatureScheme::ED25519,
            rustls::SignatureScheme::ECDSA_NISTP256_SHA256,
            rustls::SignatureScheme::ECDSA_NISTP384_SHA384,
            rustls::SignatureScheme::RSA_PSS_SHA256,
            rustls::SignatureScheme::RSA_PKCS1_SHA256,
        ]
    }
}

/// Creates a dynamic QUIC listener on the remote device via RemotePairing control channel.
/// Returns the dynamic UDP port for native QUIC connection, and retains the control connection fd.
///
/// # Safety
/// All pointer arguments must be valid and non-null (except `out_control_fd`).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rppairing_create_quic_listener(
    addr: *const idevice_sockaddr,
    addr_len: idevice_socklen_t,
    hostname: *const c_char,
    pairing_file: *mut RpPairingFileHandle,
    client_pub_b64: *const c_char,
    out_port: *mut u16,
    out_control_fd: *mut i32,
) -> *mut IdeviceFfiError {
    if addr.is_null()
        || hostname.is_null()
        || pairing_file.is_null()
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
    let rpf = unsafe { &mut (*pairing_file).0 };

    let res = run_sync_local(async {
        let stream = run_global_timeout(|| tokio::net::TcpStream::connect(socket_addr))
            .await
            .map_err(|e| IdeviceError::InternalError(format!("connect: {e}")))?;
        let conn = RpPairingSocket::new(stream);

        let mut rpc = RemotePairingClient::new(conn, &host);
        rpc.connect(rpf, async || String::new()).await?;

        let port = rpc.create_quic_listener(&client_pub).await?;
        let tcp = rpc.into_inner().inner;

        #[cfg(unix)]
        {
            use std::os::unix::io::IntoRawFd;
            let std_stream = tcp.into_std().map_err(|e| IdeviceError::InternalError(format!("into_std: {e}")))?;
            let fd = std_stream.into_raw_fd();
            Ok::<_, IdeviceError>((port, fd))
        }
        #[cfg(not(unix))]
        {
            Ok::<_, IdeviceError>((port, -1))
        }
    });

    match res {
        Ok((port, fd)) => {
            unsafe {
                *out_port = port;
                if !out_control_fd.is_null() {
                    *out_control_fd = fd;
                }
            }
            null_mut()
        }
        Err(e) => ffi_err!(e),
    }
}

/// Connects to a remote pairing QUIC listener, performs the QUIC + TLS 1.3 handshake
/// with Quinn, sends the 1024-byte initial datagram, exchanges CDTunnel handshake,
/// and returns the negotiated inner IPv6 addresses and RSD port.
///
/// # Safety
/// All pointers must be valid and non-null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rppairing_connect_quic_tunnel(
    addr: *const idevice_sockaddr,
    addr_len: idevice_socklen_t,
    hostname: *const c_char,
    pairing_file: *mut RpPairingFileHandle,
    client_pub_b64: *const c_char,
    client_cert_der: *const u8,
    client_cert_len: usize,
    client_key_der: *const u8,
    client_key_len: usize,
    out_client_ip: *mut c_char,
    client_ip_len: usize,
    out_server_ip: *mut c_char,
    server_ip_len: usize,
    out_rsd_port: *mut u16,
) -> *mut IdeviceFfiError {
    if addr.is_null()
        || hostname.is_null()
        || pairing_file.is_null()
        || client_pub_b64.is_null()
        || client_cert_der.is_null()
        || client_key_der.is_null()
        || out_client_ip.is_null()
        || out_server_ip.is_null()
        || out_rsd_port.is_null()
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
    let rpf = unsafe { &mut (*pairing_file).0 };

    let cert_slice = unsafe { std::slice::from_raw_parts(client_cert_der, client_cert_len) };
    let key_slice = unsafe { std::slice::from_raw_parts(client_key_der, client_key_len) };

    let cert_der = rustls::pki_types::CertificateDer::from(cert_slice.to_vec());
    let key_der = rustls::pki_types::PrivateKeyDer::Pkcs8(key_slice.to_vec().into());

    let res = run_sync_local(async move {
        // Step 1: Connect control stream & pair verify
        let stream = run_global_timeout(|| tokio::net::TcpStream::connect(socket_addr))
            .await
            .map_err(|e| IdeviceError::InternalError(format!("connect: {e}")))?;
        let conn = RpPairingSocket::new(stream);

        let mut rpc = RemotePairingClient::new(conn, &host);
        rpc.connect(rpf, async || String::new()).await?;

        // Step 2: Request QUIC listener
        let quic_port = rpc.create_quic_listener(&client_pub).await?;
        tracing::debug!("[RP-QUIC] Dynamic QUIC listener created on port {quic_port}");

        // Step 3: Build rustls client config with SkipServerVerification (includes 0x0807 Ed25519)
        let mut crypto = rustls::ClientConfig::builder()
            .dangerous()
            .with_custom_certificate_verifier(std::sync::Arc::new(SkipServerVerification))
            .with_client_auth_cert(vec![cert_der], key_der)
            .map_err(|e| IdeviceError::InternalError(format!("rustls crypto config: {e}")))?;
        crypto.alpn_protocols = vec![b"RemotePairingTunnelProtocol".to_vec()];

        let quic_crypto = quinn::crypto::rustls::QuicClientConfig::try_from(crypto)
            .map_err(|e| IdeviceError::InternalError(format!("quic crypto config: {e}")))?;
        let mut client_config = quinn::ClientConfig::new(std::sync::Arc::new(quic_crypto));

        let mut transport = quinn::TransportConfig::default();
        transport.max_idle_timeout(Some(std::time::Duration::from_secs(30).try_into().unwrap()));
        transport.datagram_receive_buffer_size(Some(65536));
        client_config.transport_config(std::sync::Arc::new(transport));

        // Step 4: Bind UDP endpoint & connect QUIC
        let mut endpoint = quinn::Endpoint::client("0.0.0.0:0".parse().unwrap())
            .map_err(|e| IdeviceError::InternalError(format!("quinn endpoint: {e}")))?;
        endpoint.set_default_client_config(client_config);

        let quic_target = std::net::SocketAddr::new(socket_addr.ip(), quic_port);
        let connecting = endpoint
            .connect(quic_target, "CoreDevice")
            .map_err(|e| IdeviceError::InternalError(format!("quinn connect to {quic_target}: {e}")))?;

        let quic_conn = connecting
            .await
            .map_err(|e| IdeviceError::InternalError(format!("quinn handshake: {e}")))?;
        tracing::debug!("[RP-QUIC] Quinn QUIC connection established!");

        // Step 5: Send 1024-byte initial datagram frame
        let _ = quic_conn.send_datagram(vec![0u8; 1024].into());

        // Step 6: Open bidirectional stream & send CDTunnel handshake
        let (mut send_stream, mut recv_stream) = quic_conn
            .open_bi()
            .await
            .map_err(|e| IdeviceError::InternalError(format!("quinn open_bi: {e}")))?;

        let req = serde_json::json!({
            "type": "clientHandshakeRequest",
            "mtu": 1420
        });
        let body = serde_json::to_vec(&req)
            .map_err(|e| IdeviceError::InternalError(format!("json encode: {e}")))?;

        let mut pkt = Vec::new();
        pkt.extend_from_slice(b"CDTunnel");
        pkt.extend_from_slice(&(body.len() as u16).to_be_bytes());
        pkt.extend_from_slice(&body);

        use tokio::io::AsyncWriteExt;
        send_stream
            .write_all(&pkt)
            .await
            .map_err(|e| IdeviceError::InternalError(format!("send CDTunnel request: {e}")))?;

        // Step 7: Read CDTunnel handshake response
        use tokio::io::AsyncReadExt;
        let mut header = [0u8; 10];
        recv_stream
            .read_exact(&mut header)
            .await
            .map_err(|e| IdeviceError::InternalError(format!("recv CDTunnel header: {e}")))?;

        if &header[..8] != b"CDTunnel" {
            return Err(IdeviceError::UnexpectedResponse(
                "CDTunnel response missing magic header".into(),
            ));
        }
        let resp_len = u16::from_be_bytes([header[8], header[9]]) as usize;
        let mut resp_bytes = vec![0u8; resp_len];
        recv_stream
            .read_exact(&mut resp_bytes)
            .await
            .map_err(|e| IdeviceError::InternalError(format!("recv CDTunnel body: {e}")))?;

        let resp: serde_json::Value = serde_json::from_slice(&resp_bytes)
            .map_err(|e| IdeviceError::InternalError(format!("parse CDTunnel json: {e}")))?;

        let client_params = resp
            .get("clientParameters")
            .ok_or_else(|| IdeviceError::UnexpectedResponse("missing clientParameters".into()))?;

        let client_addr = client_params
            .get("address")
            .and_then(|a| a.as_str())
            .ok_or_else(|| IdeviceError::UnexpectedResponse("missing client address".into()))?
            .to_string();

        let server_addr = resp
            .get("serverAddress")
            .and_then(|a| a.as_str())
            .ok_or_else(|| IdeviceError::UnexpectedResponse("missing server address".into()))?
            .to_string();

        let rsd_port = resp
            .get("serverRSDPort")
            .and_then(|p| p.as_u64())
            .unwrap_or(0) as u16;

        Ok::<_, IdeviceError>((client_addr, server_addr, rsd_port))
    });

    match res {
        Ok((client_addr, server_addr, rsd_port)) => {
            let c_client = std::ffi::CString::new(client_addr).unwrap_or_default();
            let c_server = std::ffi::CString::new(server_addr).unwrap_or_default();
            unsafe {
                libc::strncpy(out_client_ip, c_client.as_ptr(), client_ip_len.saturating_sub(1));
                libc::strncpy(out_server_ip, c_server.as_ptr(), server_ip_len.saturating_sub(1));
                *out_rsd_port = rsd_port;
            }
            null_mut()
        }
        Err(e) => ffi_err!(e),
    }
}
'''
    text += ffi_functions
    path.write_text(text, encoding="utf-8")
    print("Successfully patched ffi/src/tunnel_provider.rs with rppairing_connect_quic_tunnel")

def patch_idevice_h(root: Path) -> None:
    path = root / "ffi" / "idevice.h"
    if not path.exists():
        print("ffi/idevice.h does not exist before build (will be generated by cbindgen), skipping")
        return
    text = path.read_text(encoding="utf-8")
    if "rppairing_connect_quic_tunnel" in text:
        print("ffi/idevice.h already has rppairing_connect_quic_tunnel")
        return

    sig = '''
/**
 * Creates a dynamic QUIC listener on the remote device via RemotePairing control channel.
 * Returns the dynamic UDP port for native QUIC connection, and retains the control connection fd.
 */
struct IdeviceFfiError *rppairing_create_quic_listener(const idevice_sockaddr *addr,
                                                       idevice_socklen_t addr_len,
                                                       const char *hostname,
                                                       struct RpPairingFileHandle *pairing_file,
                                                       const char *client_pub_b64,
                                                       uint16_t *out_port,
                                                       int32_t *out_control_fd);

/**
 * Connects to a remote pairing QUIC listener, performs the QUIC + TLS 1.3 handshake
 * with Quinn, sends the 1024-byte initial datagram, exchanges CDTunnel handshake,
 * and returns the negotiated inner IPv6 addresses and RSD port.
 */
struct IdeviceFfiError *rppairing_connect_quic_tunnel(const idevice_sockaddr *addr,
                                                      idevice_socklen_t addr_len,
                                                      const char *hostname,
                                                      struct RpPairingFileHandle *pairing_file,
                                                      const char *client_pub_b64,
                                                      const uint8_t *client_cert_der,
                                                      size_t client_cert_len,
                                                      const uint8_t *client_key_der,
                                                      size_t client_key_len,
                                                      char *out_client_ip,
                                                      size_t client_ip_len,
                                                      char *out_server_ip,
                                                      size_t server_ip_len,
                                                      uint16_t *out_rsd_port);
'''
    text += sig
    path.write_text(text, encoding="utf-8")
    print("Successfully patched ffi/idevice.h with rppairing_connect_quic_tunnel declaration")

def patch_coredevice_proxy_nossl(root: Path) -> None:
    path = root / "idevice" / "src" / "lib.rs"
    if not path.exists():
        die(f"idevice/src/lib.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if 'Self::service_name() != "com.apple.internal.devicecompute.CoreDeviceProxy"' in text:
        print("idevice/src/lib.rs already has CoreDeviceProxy raw CDTunnel fix")
        return

    old = '''        if ssl {
            idevice
                .start_session(&provider.get_pairing_file().await?, legacy)
                .await?;
        }'''
    new = '''        if ssl && Self::service_name() != "com.apple.internal.devicecompute.CoreDeviceProxy" {
            idevice
                .start_session(&provider.get_pairing_file().await?, legacy)
                .await?;
        }'''
    text = once(text, old, new, "disable SSL on CoreDeviceProxy service in lib.rs")
    path.write_text(text, encoding="utf-8")
    print("Successfully patched idevice/src/lib.rs to preserve raw CDTunnel on CoreDeviceProxy")

def main():
    if len(sys.argv) < 2:
        die("Usage: patch_v28_quic_coredevice.py <idevice-repo-root>")
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        die(f"Directory does not exist: {root}")
    patch_remote_pairing_mod(root)
    patch_ffi_cargo_toml(root)
    patch_tunnel_provider(root)
    patch_idevice_h(root)
    patch_coredevice_proxy_nossl(root)
    print("ALL V28 QUIC COREDEVICE PATCHES APPLIED SUCCESSFULLY!")

if __name__ == '__main__':
    main()

