#!/usr/bin/env python3
"""Apply the proven LocalDevVPN -> CoreDeviceProxy transport fixes to idevice."""

from __future__ import annotations

import os
from pathlib import Path
import sys


MARKER = "[SIDESTORE_COREDEVICE]"


def die(message: str) -> None:
    raise SystemExit(message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_cdtunnel(root: Path) -> None:
    path = root / "idevice" / "src" / "tunnel.rs"
    text = path.read_text(encoding="utf-8")
    if "packet.extend_from_slice(CDTUNNEL_MAGIC);" not in text:
        text = replace_once(
            text,
            """        stream.write_all(CDTUNNEL_MAGIC).await?;
        stream.write_all(&(body.len() as u16).to_be_bytes()).await?;
        stream.write_all(&body).await?;
        stream.flush().await?;""",
            """        let mut packet = Vec::with_capacity(CDTUNNEL_MAGIC.len() + 2 + body.len());
        packet.extend_from_slice(CDTUNNEL_MAGIC);
        packet.extend_from_slice(&(body.len() as u16).to_be_bytes());
        packet.extend_from_slice(&body);
        stream.write_all(&packet).await?;
        stream.flush().await?;""",
            "contiguous CDTunnel request",
        )
        path.write_text(text, encoding="utf-8")


def patch_afc_runtime(root: Path) -> None:
    path = root / "ffi" / "src" / "afc.rs"
    text = path.read_text(encoding="utf-8")
    replacements = (
        (
            "let res: Result<(), IdeviceError> = run_sync(async move { fd.close().await });",
            "let res: Result<(), IdeviceError> = run_sync_local(async move { fd.close().await });",
        ),
        (
            "let res: Result<Vec<u8>, IdeviceError> = run_sync({",
            "let res: Result<Vec<u8>, IdeviceError> = run_sync_local({",
        ),
        (
            "let res: Result<Vec<u8>, IdeviceError> = run_sync(async move { fd.read_entire().await });",
            "let res: Result<Vec<u8>, IdeviceError> = run_sync_local(async move { fd.read_entire().await });",
        ),
        (
            "let res: Result<u64, IdeviceError> = run_sync(async move { Ok(fd.seek(seek_from).await?) });",
            "let res: Result<u64, IdeviceError> = run_sync_local(async move { Ok(fd.seek(seek_from).await?) });",
        ),
        (
            "run_sync(async { Ok(fd.seek(SeekFrom::Current(0)).await?) });",
            "run_sync_local(async { Ok(fd.seek(SeekFrom::Current(0)).await?) });",
        ),
        (
            "let res: Result<(), IdeviceError> = run_sync(async move { fd.write_entire(data_slice).await });",
            "let res: Result<(), IdeviceError> = run_sync_local(async move { fd.write_entire(data_slice).await });",
        ),
    )
    for old, new in replacements:
        if new in text:
            continue
        text = replace_once(text, old, new, f"AFC local runtime: {old[:45]}")
    path.write_text(text, encoding="utf-8")


def patch_plist_array_free(root: Path) -> None:
    path = root / "ffi" / "src" / "lib.rs"
    text = path.read_text(encoding="utf-8")
    old = """pub unsafe extern \"C\" fn idevice_plist_array_free(plists: *mut plist_t, len: usize) {
    if !plists.is_null() {
        let data = unsafe { std::slice::from_raw_parts(plists, len) };
        for x in data {
            unsafe { plist_ffi::creation::plist_free((*x) as *mut PlistWrapper) };
        }
    }
}"""
    new = """pub unsafe extern \"C\" fn idevice_plist_array_free(plists: *mut plist_t, len: usize) {
    if !plists.is_null() {
        let slice = std::ptr::slice_from_raw_parts_mut(plists, len);
        let data = unsafe { Box::from_raw(slice) };
        for x in data.iter() {
            unsafe { plist_ffi::creation::plist_free((*x) as *mut PlistWrapper) };
        }
    }
}"""
    if "Box::from_raw(slice)" not in text:
        text = replace_once(text, old, new, "plist array ownership")
        path.write_text(text, encoding="utf-8")


def patch_tunnel_provider(root: Path) -> None:
    path = root / "ffi" / "src" / "tunnel_provider.rs"
    text = path.read_text(encoding="utf-8")

    legacy_logger = r'''#[cfg(target_os = "ios")]
unsafe extern "C" {
    fn lockdown_diag_rust_log(message: *const std::ffi::c_char);
}

#[cfg(target_os = "ios")]
fn transport_log(message: &str) {
    if let Ok(message) = std::ffi::CString::new(message) {
        unsafe { lockdown_diag_rust_log(message.as_ptr()) };
    }
}

#[cfg(not(target_os = "ios"))]
fn transport_log(message: &str) {
    tracing::debug!("{message}");
}'''
    owned_logger = r'''static TRANSPORT_LOG_CALLBACK: Mutex<
    Option<unsafe extern "C" fn(*const std::ffi::c_char)>,
> = Mutex::new(None);

/// Registers the host application's transport logger. The callback must remain
/// valid until it is replaced or cleared.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn idevice_set_transport_log_callback(
    callback: Option<unsafe extern "C" fn(*const std::ffi::c_char)>,
) {
    *TRANSPORT_LOG_CALLBACK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = callback;
}

/// Rust owns this symbol because both idevice and its static jktcp dependency
/// call it. Keeping it in the same archive prevents an unresolved cross-library
/// reference when Swift package objects are linked later.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn lockdown_diag_rust_log(message: *const std::ffi::c_char) {
    if message.is_null() {
        return;
    }

    let callback = *TRANSPORT_LOG_CALLBACK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(callback) = callback {
        unsafe { callback(message) };
    } else if let Ok(message) = unsafe { std::ffi::CStr::from_ptr(message) }.to_str() {
        tracing::debug!("{message}");
    }
}

fn transport_log(message: &str) {
    if let Ok(message) = std::ffi::CString::new(message) {
        unsafe { lockdown_diag_rust_log(message.as_ptr()) };
    }
}'''

    if legacy_logger in text:
        text = text.replace(legacy_logger, owned_logger, 1)
        path.write_text(text, encoding="utf-8")

    if MARKER in text:
        required = [
            "HeartbeatClient::connect",
            "idevice_set_transport_log_callback",
            "pub unsafe extern \"C\" fn lockdown_diag_rust_log",
            "tunnel_heartbeat_is_active",
            ".min(1340)",
            "TUNNEL_RSD_HANDSHAKE_PASS",
        ]
        missing = [item for item in required if item not in text]
        if missing:
            die(f"CoreDevice marker present but patch is incomplete: {missing}")
        return

    state = r'''
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::task::JoinHandle;

''' + owned_logger + r'''

static ACTIVE_HEARTBEAT: Mutex<Option<JoinHandle<()>>> = Mutex::new(None);
static HEARTBEAT_IS_ACTIVE: AtomicBool = AtomicBool::new(false);

#[unsafe(no_mangle)]
pub unsafe extern "C" fn tunnel_heartbeat_is_active() -> bool {
    HEARTBEAT_IS_ACTIVE.load(Ordering::SeqCst)
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn tunnel_heartbeat_stop() {
    let mut heartbeat = ACTIVE_HEARTBEAT.lock().unwrap();
    if let Some(handle) = heartbeat.take() {
        handle.abort();
    }
    HEARTBEAT_IS_ACTIVE.store(false, Ordering::SeqCst);
    transport_log("[SIDESTORE_COREDEVICE] HEARTBEAT_STOPPED");
}
'''
    text = replace_once(
        text,
        "use crate::{IdeviceFfiError, ffi_err, provider::IdeviceProviderHandle, run_sync_local};",
        "use crate::{IdeviceFfiError, ffi_err, provider::IdeviceProviderHandle, run_sync_local};\n" + state,
        "CoreDevice heartbeat state",
    )

    old = """        let provider_ref: &dyn IdeviceProvider = unsafe { &*(*lockdown_provider).0 };
        let proxy = CoreDeviceProxy::connect(provider_ref).await?;
        let rsd_port = proxy.tunnel_info().server_rsd_port;
        let adapter = proxy
            .create_software_tunnel()
            .map_err(|e| IdeviceError::InternalError(format!("{e}")))?;
        let mut adapter = adapter.to_async_handle();
        let rsd_stream = adapter
            .connect(rsd_port)
            .await
            .map_err(|e| IdeviceError::InternalError(format!("{e}")))?;
        let handshake = RsdHandshake::new(rsd_stream).await?;
        Ok::<_, IdeviceError>((adapter, handshake))"""

    new = r'''        let provider_ref: &dyn IdeviceProvider = unsafe { &*(*lockdown_provider).0 };

        unsafe { tunnel_heartbeat_stop() };
        use idevice::heartbeat::HeartbeatClient;
        let mut heartbeat = match HeartbeatClient::connect(provider_ref).await {
            Ok(client) => {
                transport_log("[SIDESTORE_COREDEVICE] HEARTBEAT_CONNECT_PASS");
                client
            }
            Err(error) => {
                transport_log(&format!("[SIDESTORE_COREDEVICE] HEARTBEAT_CONNECT_FAIL error={error}"));
                return Err(IdeviceError::InternalError(format!(
                    "CoreDevice heartbeat connection failed: {error}"
                )));
            }
        };

        HEARTBEAT_IS_ACTIVE.store(true, Ordering::SeqCst);
        let heartbeat_task = tokio::spawn(async move {
            loop {
                match heartbeat.get_marco(60).await {
                    Ok(_) => {
                        if let Err(error) = heartbeat.send_polo().await {
                            transport_log(&format!(
                                "[SIDESTORE_COREDEVICE] HEARTBEAT_POLO_FAIL error={error}"
                            ));
                            break;
                        }
                    }
                    Err(error) => {
                        transport_log(&format!(
                            "[SIDESTORE_COREDEVICE] HEARTBEAT_MARCO_FAIL error={error}"
                        ));
                        break;
                    }
                }
            }
            HEARTBEAT_IS_ACTIVE.store(false, Ordering::SeqCst);
        });
        *ACTIVE_HEARTBEAT.lock().unwrap() = Some(heartbeat_task);

        tokio::time::sleep(std::time::Duration::from_millis(400)).await;
        transport_log("[SIDESTORE_COREDEVICE] TUNNEL_COREDEVICE_CONNECT_START");
        let proxy = match tokio::time::timeout(
            std::time::Duration::from_secs(20),
            CoreDeviceProxy::connect(provider_ref),
        )
        .await
        {
            Ok(Ok(proxy)) => {
                transport_log("[SIDESTORE_COREDEVICE] TUNNEL_COREDEVICE_CONNECT_PASS");
                proxy
            }
            Ok(Err(error)) => {
                unsafe { tunnel_heartbeat_stop() };
                return Err(error);
            }
            Err(_) => {
                unsafe { tunnel_heartbeat_stop() };
                return Err(IdeviceError::InternalError(
                    "CoreDeviceProxy connect timed out after 20 seconds".into(),
                ));
            }
        };

        let negotiated_mtu = proxy.tunnel_info().mtu as usize;
        let rsd_port = proxy.tunnel_info().server_rsd_port;
        let mut adapter = match proxy.create_software_tunnel() {
            Ok(adapter) => adapter,
            Err(error) => {
                unsafe { tunnel_heartbeat_stop() };
                return Err(IdeviceError::InternalError(format!(
                    "CoreDevice software tunnel failed: {error}"
                )));
            }
        };

        // Apple CoreDeviceProxy drops large host-to-device IPv6 packets even
        // when it advertises a 16 KB tunnel MTU. jktcp has no PLPMTUD, so use
        // the reliable 1400-byte IPv6 MTU seed (1340-byte TCP MSS).
        let effective_mss = negotiated_mtu.saturating_sub(60).min(1340);
        adapter.set_mss(effective_mss);
        transport_log(&format!(
            "[SIDESTORE_COREDEVICE] JKTCP_MSS_CONFIG negotiated_mtu={negotiated_mtu} effective_mss={effective_mss}"
        ));
        let mut adapter = adapter.to_async_handle();

        let rsd_stream = match tokio::time::timeout(
            std::time::Duration::from_secs(12),
            adapter.connect(rsd_port),
        )
        .await
        {
            Ok(Ok(stream)) => stream,
            Ok(Err(error)) => {
                unsafe { tunnel_heartbeat_stop() };
                return Err(IdeviceError::InternalError(format!("RSD connect failed: {error}")));
            }
            Err(_) => {
                unsafe { tunnel_heartbeat_stop() };
                return Err(IdeviceError::InternalError(
                    "RSD connect timed out after 12 seconds".into(),
                ));
            }
        };

        let handshake = match tokio::time::timeout(
            std::time::Duration::from_secs(15),
            RsdHandshake::new(rsd_stream),
        )
        .await
        {
            Ok(Ok(handshake)) => handshake,
            Ok(Err(error)) => {
                unsafe { tunnel_heartbeat_stop() };
                return Err(error);
            }
            Err(_) => {
                unsafe { tunnel_heartbeat_stop() };
                return Err(IdeviceError::InternalError(
                    "RSD handshake timed out after 15 seconds".into(),
                ));
            }
        };
        transport_log("[SIDESTORE_COREDEVICE] TUNNEL_RSD_HANDSHAKE_PASS");
        Ok::<_, IdeviceError>((adapter, handshake))'''
    text = replace_once(text, old, new, "CoreDevice tunnel implementation")
    path.write_text(text, encoding="utf-8")

    free_path = root / "ffi" / "src" / "core_device_proxy.rs"
    free_text = free_path.read_text(encoding="utf-8")
    hook = "crate::tunnel_provider::tunnel_heartbeat_stop();"
    if hook not in free_text:
        free_text = replace_once(
            free_text,
            """    if !handle.is_null() {
        tracing::debug!("Freeing adapter");
        let _ = unsafe { Box::from_raw(handle) };""",
            """    if !handle.is_null() {
        tracing::debug!("Freeing adapter");
        crate::tunnel_provider::tunnel_heartbeat_stop();
        let _ = unsafe { Box::from_raw(handle) };""",
            "heartbeat teardown on adapter free",
        )
        free_path.write_text(free_text, encoding="utf-8")


def patch_jktcp_dependency(root: Path, jktcp_root: Path) -> None:
    path = root / "idevice" / "Cargo.toml"
    text = path.read_text(encoding="utf-8")
    relative = os.path.relpath(jktcp_root, path.parent).replace("\\", "/")
    local = f'jktcp = {{ path = "{relative}", optional = true, default-features = false }}'
    if local not in text:
        text = replace_once(
            text,
            'jktcp = { git = "https://github.com/SideStore/jktcp", branch = "master", optional = true, default-features = false }',
            local,
            "local jktcp dependency",
        )
        path.write_text(text, encoding="utf-8")


def verify(root: Path) -> None:
    checks = {
        root / "idevice" / "src" / "tunnel.rs": [
            "packet.extend_from_slice(CDTUNNEL_MAGIC);",
            "stream.write_all(&packet).await?;",
        ],
        root / "ffi" / "src" / "tunnel_provider.rs": [
            MARKER,
            "HeartbeatClient::connect",
            "idevice_set_transport_log_callback",
            "pub unsafe extern \"C\" fn lockdown_diag_rust_log",
            "tunnel_heartbeat_is_active",
            ".min(1340)",
            "TUNNEL_RSD_HANDSHAKE_PASS",
        ],
        root / "ffi" / "src" / "afc.rs": [
            "run_sync_local(async move { fd.write_entire(data_slice).await })",
            "run_sync_local(async move { fd.close().await })",
        ],
        root / "ffi" / "src" / "lib.rs": [
            "let data = unsafe { Box::from_raw(slice) };",
        ],
    }
    for path, needles in checks.items():
        text = path.read_text(encoding="utf-8")
        missing = [needle for needle in needles if needle not in text]
        if missing:
            die(f"{path}: verification failed, missing {missing}")

    forbidden = ["rppairing_connect_quic_tunnel", "quinn::", "create_quic_listener"]
    joined = "\n".join(path.read_text(encoding="utf-8") for path in checks)
    present = [needle for needle in forbidden if needle in joined]
    if present:
        die(f"QUIC code is forbidden in the production transport patch: {present}")


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: patch_v29_coredevice_idevice.py <idevice-root> <jktcp-root>")
    root = Path(sys.argv[1]).resolve()
    jktcp_root = Path(sys.argv[2]).resolve()
    if not (root / "ffi" / "Cargo.toml").is_file():
        die(f"invalid idevice checkout: {root}")
    if not (jktcp_root / "src" / "adapter.rs").is_file():
        die(f"invalid jktcp checkout: {jktcp_root}")

    patch_cdtunnel(root)
    patch_afc_runtime(root)
    patch_plist_array_free(root)
    patch_tunnel_provider(root)
    patch_jktcp_dependency(root, jktcp_root)
    verify(root)
    print("V29 CoreDevice idevice patch applied and verified")


if __name__ == "__main__":
    main()
