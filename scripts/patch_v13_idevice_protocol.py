#!/usr/bin/env python3
"""Patch pinned SideStore/idevice for the v13 Lockdown/CoreDevice transport.

The v12 device log proved that lockdownd acknowledged exactly the four-byte
length prefix and reset before the XML payload arrived.  This patch coalesces
that prefix and payload into one write, performs QueryType before every other
Lockdown request in the CoreDevice bootstrap, and emits secret-free stage
markers from the native implementation.
"""

from __future__ import annotations

from pathlib import Path
import sys

MARKER = "[SS-V13-IDEVICE]"


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected exactly one source anchor, found {count}")
    return text.replace(old, new, 1)


def verify(root: Path) -> None:
    lib_path = root / "idevice" / "src" / "lib.rs"
    provider_path = root / "idevice" / "src" / "provider.rs"
    tunnel_path = root / "ffi" / "src" / "tunnel_provider.rs"
    for path in (lib_path, provider_path, tunnel_path):
        if not path.exists():
            die(f"v13 IDevice verification: missing {path}")

    lib = lib_path.read_text()
    provider = provider_path.read_text()
    tunnel = tunnel_path.read_text()

    required_lib = [
        MARKER,
        "fn v13_frame_prefixed_payload",
        "combined=true",
        "socket.write_all(&framed).await?",
        "v13_frame_prefix_round_trip",
    ]
    required_provider = [
        "stream.set_nodelay(true)?",
        "TCP_NODELAY_ENABLED",
    ]
    required_tunnel = [
        "LOCKDOWN_TCP_CONNECT_START",
        "LOCKDOWN_TCP_CONNECT_SUCCESS",
        "LOCKDOWN_QUERY_TYPE_START",
        "LOCKDOWN_QUERY_TYPE_SUCCESS",
        "LOCKDOWN_START_SESSION_START",
        "LOCKDOWN_START_SESSION_SUCCESS",
        "COREDEVICE_START_SERVICE_START",
        "COREDEVICE_START_SERVICE_SUCCESS",
        "COREDEVICE_SERVICE_CONNECT_START",
        "COREDEVICE_SERVICE_CONNECT_SUCCESS",
        "COREDEVICE_CDTUNNEL_START",
        "COREDEVICE_CDTUNNEL_SUCCESS",
        "COREDEVICE_RSD_CONNECT_START",
        "COREDEVICE_RSD_CONNECT_SUCCESS",
        "COREDEVICE_RSD_HANDSHAKE_SUCCESS",
    ]

    missing = [item for item in required_lib if item not in lib]
    missing += [item for item in required_provider if item not in provider]
    missing += [item for item in required_tunnel if item not in tunnel]
    if missing:
        die(f"v13 IDevice verification failed; missing: {missing}")

    if lib.count("socket.write_all(&framed).await?") != 2:
        die("v13 IDevice verification failed: XML and binary plist writes must both use one framed write")
    for stale in [
        "socket.write_all(&len.to_be_bytes()).await?",
        "socket.write_all(message.as_bytes()).await?",
        "socket.write_all(&message).await?",
    ]:
        if stale in lib:
            die(f"v13 IDevice verification failed: split plist write remains: {stale}")

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
    positions = [tunnel.find(item) for item in ordered]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        die(f"v13 IDevice verification failed: protocol stages are out of order: {positions}")

    usb_start = tunnel.find('pub unsafe extern "C" fn tunnel_create_usb(')
    usb_end = tunnel.find("/// Pairs via USB CoreDeviceProxy", usb_start)
    if usb_start < 0 or usb_end < 0:
        die("v13 IDevice verification failed: tunnel_create_usb boundaries missing")
    usb_body = tunnel[usb_start:usb_end]
    if "CoreDeviceProxy::connect(provider_ref)" in usb_body:
        die("v13 IDevice verification failed: generic CoreDevice connect bypasses QueryType diagnostics")
    if "lockdownd_pair" in usb_body or "PairRecord" in usb_body:
        die("v13 IDevice verification failed: CoreDevice path must never pair on-device")

    forbidden_logging = [
        "HostPrivateKey",
        "RootPrivateKey",
        "private_key=",
        "certificate=",
        "pairing_file={",
        "UDID=",
    ]
    native_log_lines = "\n".join(
        line for line in (lib + "\n" + provider + "\n" + tunnel).splitlines() if MARKER in line
    )
    leaked = [item for item in forbidden_logging if item in native_log_lines]
    if leaked:
        die(f"v13 IDevice verification failed: sensitive native log marker: {leaked}")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v13_idevice_protocol.py <idevice-root>")

    root = Path(sys.argv[1])
    lib_path = root / "idevice" / "src" / "lib.rs"
    provider_path = root / "idevice" / "src" / "provider.rs"
    tunnel_path = root / "ffi" / "src" / "tunnel_provider.rs"
    for path in (lib_path, provider_path, tunnel_path):
        if not path.exists():
            die(f"missing expected IDevice source: {path}")

    if MARKER in lib_path.read_text() or MARKER in tunnel_path.read_text():
        verify(root)
        print("v13 IDevice protocol patch already present and verified")
        return

    lib = lib_path.read_text()
    helper_anchor = "impl Idevice {\n"
    helper = r'''fn v13_frame_prefixed_payload(payload: &[u8]) -> Result<Vec<u8>, IdeviceError> {
    if payload.len() > u32::MAX as usize {
        return Err(IdeviceError::UnexpectedResponse(
            "plist payload exceeds the 32-bit Lockdown frame limit".to_string(),
        ));
    }

    let mut framed = Vec::with_capacity(4 + payload.len());
    framed.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    framed.extend_from_slice(payload);
    Ok(framed)
}

'''
    if lib.count(helper_anchor) != 1:
        die(f"lib.rs helper anchor: expected once, found {lib.count(helper_anchor)}")
    lib = lib.replace(helper_anchor, helper + helper_anchor, 1)

    old_xml = '''            let message = String::from_utf8(message)?;
            let len = message.len() as u32;
            socket.write_all(&len.to_be_bytes()).await?;
            socket.write_all(message.as_bytes()).await?;
            socket.flush().await?;
'''
    new_xml = '''            let message = String::from_utf8(message)?;
            let framed = v13_frame_prefixed_payload(message.as_bytes())?;
            if self.label.contains("CoreDeviceProxy") {
                tracing::error!(
                    "[SS-V13-IDEVICE] LOCKDOWN_FRAME_WRITE format=xml payload_len={} frame_len={} combined=true",
                    message.len(),
                    framed.len()
                );
            }
            socket.write_all(&framed).await?;
            socket.flush().await?;
'''
    lib = replace_once(lib, old_xml, new_xml, "XML plist framing")

    old_binary = '''            let message = writer.into_inner().unwrap();
            let len = message.len() as u32;
            socket.write_all(&len.to_be_bytes()).await?;
            socket.write_all(&message).await?;
            socket.flush().await?;
'''
    new_binary = '''            let message = writer.into_inner().unwrap();
            let framed = v13_frame_prefixed_payload(&message)?;
            if self.label.contains("CoreDeviceProxy") {
                tracing::error!(
                    "[SS-V13-IDEVICE] LOCKDOWN_FRAME_WRITE format=binary payload_len={} frame_len={} combined=true",
                    message.len(),
                    framed.len()
                );
            }
            socket.write_all(&framed).await?;
            socket.flush().await?;
'''
    lib = replace_once(lib, old_binary, new_binary, "binary plist framing")

    test_anchor = "/// Errors specific to the CDTunnel protocol\n"
    test_module = r'''#[cfg(test)]
mod v13_framing_tests {
    use super::v13_frame_prefixed_payload;

    #[test]
    fn v13_frame_prefix_round_trip() {
        let payload = b"plist-frame";
        let framed = v13_frame_prefixed_payload(payload).expect("frame must build");
        assert_eq!(&framed[..4], &(payload.len() as u32).to_be_bytes());
        assert_eq!(&framed[4..], payload);
        assert_eq!(framed.len(), payload.len() + 4);
    }
}

'''
    if lib.count(test_anchor) != 1:
        die(f"lib.rs test anchor: expected once, found {lib.count(test_anchor)}")
    lib = lib.replace(test_anchor, test_module + test_anchor, 1)
    lib_path.write_text(lib)

    provider = provider_path.read_text()
    old_provider = '''            let stream = TcpStream::connect(socket_addr).await?;
            Ok(Idevice::new(Box::new(stream), label))
'''
    new_provider = '''            let stream = TcpStream::connect(socket_addr).await?;
            stream.set_nodelay(true)?;
            tracing::error!("[SS-V13-IDEVICE] TCP_NODELAY_ENABLED port={port}");
            Ok(Idevice::new(Box::new(stream), label))
'''
    provider = replace_once(provider, old_provider, new_provider, "TCP provider nodelay")
    provider_path.write_text(provider)

    tunnel = tunnel_path.read_text()
    function_start = tunnel.find('#[unsafe(no_mangle)]\npub unsafe extern "C" fn tunnel_create_usb(')
    function_end = tunnel.find("\n/// Pairs via USB CoreDeviceProxy", function_start)
    if function_start < 0 or function_end < 0:
        die("tunnel_provider.rs: could not locate tunnel_create_usb function boundaries")

    replacement = r'''#[unsafe(no_mangle)]
pub unsafe extern "C" fn tunnel_create_usb(
    lockdown_provider: *mut IdeviceProviderHandle,
    out_adapter: *mut *mut AdapterHandle,
    out_handshake: *mut *mut RsdHandshakeHandle,
) -> *mut IdeviceFfiError {
    if lockdown_provider.is_null() || out_adapter.is_null() || out_handshake.is_null() {
        return ffi_err!(IdeviceError::FfiInvalidArg);
    }

    let res = run_sync_local(async {
        use idevice::services::lockdown::LockdownClient;

        let provider_ref: &dyn IdeviceProvider = unsafe { &*(*lockdown_provider).0 };

        tracing::error!("[SS-V13-IDEVICE] LOCKDOWN_TCP_CONNECT_START port=62078");
        let mut lockdown = match LockdownClient::connect(provider_ref).await {
            Ok(client) => {
                tracing::error!("[SS-V13-IDEVICE] LOCKDOWN_TCP_CONNECT_SUCCESS port=62078");
                client
            }
            Err(error) => {
                tracing::error!("[SS-V13-IDEVICE] LOCKDOWN_TCP_CONNECT_FAILED error={error:?}");
                return Err(error);
            }
        };

        // Canonical lockdownd clients issue QueryType before GetValue or
        // StartSession.  v12 skipped this and the reflected service reset after
        // acknowledging only the four-byte plist length prefix.
        tracing::error!("[SS-V13-IDEVICE] LOCKDOWN_QUERY_TYPE_START");
        let lockdown_type = match lockdown.idevice.get_type().await {
            Ok(value) => value,
            Err(error) => {
                tracing::error!("[SS-V13-IDEVICE] LOCKDOWN_QUERY_TYPE_FAILED error={error:?}");
                return Err(error);
            }
        };
        if lockdown_type != "com.apple.mobile.lockdown" {
            let error = IdeviceError::UnexpectedResponse(format!(
                "unexpected QueryType response: {lockdown_type}"
            ));
            tracing::error!("[SS-V13-IDEVICE] LOCKDOWN_QUERY_TYPE_FAILED unexpected_type=true");
            return Err(error);
        }
        tracing::error!("[SS-V13-IDEVICE] LOCKDOWN_QUERY_TYPE_SUCCESS");

        let pairing_file = match provider_ref.get_pairing_file().await {
            Ok(value) => value,
            Err(error) => {
                tracing::error!("[SS-V13-IDEVICE] LOCKDOWN_PAIRING_CLONE_FAILED error={error:?}");
                return Err(error);
            }
        };

        tracing::error!("[SS-V13-IDEVICE] LOCKDOWN_START_SESSION_START");
        let legacy = match lockdown.start_session(&pairing_file).await {
            Ok(value) => {
                tracing::error!(
                    "[SS-V13-IDEVICE] LOCKDOWN_START_SESSION_SUCCESS legacy={value}"
                );
                value
            }
            Err(error) => {
                tracing::error!("[SS-V13-IDEVICE] LOCKDOWN_START_SESSION_FAILED error={error:?}");
                return Err(error);
            }
        };

        tracing::error!(
            "[SS-V13-IDEVICE] COREDEVICE_START_SERVICE_START service=com.apple.internal.devicecompute.CoreDeviceProxy"
        );
        let (service_port, service_ssl) = match lockdown
            .start_service(CoreDeviceProxy::service_name())
            .await
        {
            Ok(value) => {
                tracing::error!(
                    "[SS-V13-IDEVICE] COREDEVICE_START_SERVICE_SUCCESS port={} ssl={}",
                    value.0,
                    value.1
                );
                value
            }
            Err(error) => {
                tracing::error!("[SS-V13-IDEVICE] COREDEVICE_START_SERVICE_FAILED error={error:?}");
                return Err(error);
            }
        };

        tracing::error!(
            "[SS-V13-IDEVICE] COREDEVICE_SERVICE_CONNECT_START port={service_port}"
        );
        let mut service_device = match provider_ref.connect(service_port).await {
            Ok(value) => value,
            Err(error) => {
                tracing::error!("[SS-V13-IDEVICE] COREDEVICE_SERVICE_CONNECT_FAILED error={error:?}");
                return Err(error);
            }
        };
        if service_ssl {
            tracing::error!("[SS-V13-IDEVICE] COREDEVICE_SERVICE_TLS_START");
            if let Err(error) = service_device.start_session(&pairing_file, legacy).await {
                tracing::error!("[SS-V13-IDEVICE] COREDEVICE_SERVICE_TLS_FAILED error={error:?}");
                return Err(error);
            }
            tracing::error!("[SS-V13-IDEVICE] COREDEVICE_SERVICE_TLS_SUCCESS");
        }
        tracing::error!(
            "[SS-V13-IDEVICE] COREDEVICE_SERVICE_CONNECT_SUCCESS port={service_port} ssl={service_ssl}"
        );

        tracing::error!("[SS-V13-IDEVICE] COREDEVICE_CDTUNNEL_START");
        let proxy = match CoreDeviceProxy::new(service_device).await {
            Ok(value) => value,
            Err(error) => {
                tracing::error!("[SS-V13-IDEVICE] COREDEVICE_CDTUNNEL_FAILED error={error:?}");
                return Err(error);
            }
        };
        let rsd_port = proxy.tunnel_info().server_rsd_port;
        tracing::error!(
            "[SS-V13-IDEVICE] COREDEVICE_CDTUNNEL_SUCCESS rsd_port={rsd_port}"
        );

        let adapter = match proxy.create_software_tunnel() {
            Ok(value) => value,
            Err(error) => {
                tracing::error!("[SS-V13-IDEVICE] COREDEVICE_ADAPTER_FAILED error={error:?}");
                return Err(error);
            }
        };
        let mut adapter = adapter.to_async_handle();

        tracing::error!("[SS-V13-IDEVICE] COREDEVICE_RSD_CONNECT_START port={rsd_port}");
        let rsd_stream = match adapter.connect(rsd_port).await {
            Ok(value) => {
                tracing::error!("[SS-V13-IDEVICE] COREDEVICE_RSD_CONNECT_SUCCESS port={rsd_port}");
                value
            }
            Err(error) => {
                let error = IdeviceError::InternalError(format!("{error}"));
                tracing::error!("[SS-V13-IDEVICE] COREDEVICE_RSD_CONNECT_FAILED error={error:?}");
                return Err(error);
            }
        };
        let handshake = match RsdHandshake::new(rsd_stream).await {
            Ok(value) => {
                tracing::error!("[SS-V13-IDEVICE] COREDEVICE_RSD_HANDSHAKE_SUCCESS");
                value
            }
            Err(error) => {
                tracing::error!("[SS-V13-IDEVICE] COREDEVICE_RSD_HANDSHAKE_FAILED error={error:?}");
                return Err(error);
            }
        };

        Ok::<_, IdeviceError>((adapter, handshake))
    });

    match res {
        Ok((adapter, handshake)) => {
            write_result(adapter, handshake, out_adapter, out_handshake);
            null_mut()
        }
        Err(error) => ffi_err!(error),
    }
}
'''
    tunnel = tunnel[:function_start] + replacement + tunnel[function_end:]
    tunnel_path.write_text(tunnel)

    verify(root)
    print("v13 IDevice frame coalescing + QueryType-first CoreDevice protocol applied and verified")


if __name__ == "__main__":
    main()
