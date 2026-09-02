#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "[SS-V17-RSD] CORE_HANDSHAKE_START"


def die(msg: str) -> None:
    raise SystemExit(msg)


def verify(text: str) -> None:
    required = [
        "[SS-V17-RSD] CONNECT_START source=established_tunnel_stream",
        "[SS-V17-RSD] CORE_HANDSHAKE_START",
        "[SS-V17-RSD] XPC_CLIENT_PASS",
        "[SS-V17-RSD] XPC_HANDSHAKE_PASS",
        "[SS-V17-RSD] DEVICE_HANDSHAKE_PASS",
        "[SS-V17-RSD] ROOT_PASS",
        "[SS-V17-RSD] HANDSHAKE_PASS",
        "tokio::time::timeout",
    ]
    missing = [x for x in required if x not in text]
    if missing:
        die(f"v17 RSD core verification missing: {missing}")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v17_rsd_core.py <idevice/src/services/rsd.rs>")
    p = Path(sys.argv[1])
    if not p.exists():
        die(f"missing file: {p}")
    text = p.read_text()
    if MARKER in text:
        verify(text)
        print("v17 RSD core phased-timeout patch already present and verified")
        return

    old = '''    pub async fn new(socket: impl ReadWrite) -> Result<Self, IdeviceError> {\n        let mut xpc_client = RemoteXpcClient::new(socket).await?;\n        xpc_client.do_handshake().await?;\n        xpc_client.send_device_handshake().await?;\n        let data = xpc_client.recv_root().await?;\n'''
    new = '''    pub async fn new(socket: impl ReadWrite) -> Result<Self, IdeviceError> {\n        tracing::error!("[SS-V17-RSD] CONNECT_START source=established_tunnel_stream");\n        tracing::error!("[SS-V17-RSD] CORE_HANDSHAKE_START");\n\n        let mut xpc_client = match tokio::time::timeout(\n            std::time::Duration::from_secs(6),\n            RemoteXpcClient::new(socket),\n        )\n        .await\n        {\n            Ok(Ok(client)) => {\n                tracing::error!("[SS-V17-RSD] XPC_CLIENT_PASS");\n                client\n            }\n            Ok(Err(error)) => {\n                tracing::error!("[SS-V17-RSD] XPC_CLIENT_FAIL error={error}");\n                return Err(error);\n            }\n            Err(_) => {\n                tracing::error!("[SS-V17-RSD] XPC_CLIENT_TIMEOUT");\n                return Err(IdeviceError::InternalError(\n                    "RSD RemoteXPC client initialization timed out".into(),\n                ));\n            }\n        };\n\n        match tokio::time::timeout(\n            std::time::Duration::from_secs(6),\n            xpc_client.do_handshake(),\n        )\n        .await\n        {\n            Ok(Ok(())) => tracing::error!("[SS-V17-RSD] XPC_HANDSHAKE_PASS"),\n            Ok(Err(error)) => {\n                tracing::error!("[SS-V17-RSD] XPC_HANDSHAKE_FAIL error={error}");\n                return Err(error);\n            }\n            Err(_) => {\n                tracing::error!("[SS-V17-RSD] XPC_HANDSHAKE_TIMEOUT");\n                return Err(IdeviceError::InternalError(\n                    "RSD RemoteXPC handshake timed out".into(),\n                ));\n            }\n        }\n\n        match tokio::time::timeout(\n            std::time::Duration::from_secs(6),\n            xpc_client.send_device_handshake(),\n        )\n        .await\n        {\n            Ok(Ok(())) => tracing::error!("[SS-V17-RSD] DEVICE_HANDSHAKE_PASS"),\n            Ok(Err(error)) => {\n                tracing::error!("[SS-V17-RSD] DEVICE_HANDSHAKE_FAIL error={error}");\n                return Err(error);\n            }\n            Err(_) => {\n                tracing::error!("[SS-V17-RSD] DEVICE_HANDSHAKE_TIMEOUT");\n                return Err(IdeviceError::InternalError(\n                    "RSD device handshake send timed out".into(),\n                ));\n            }\n        }\n\n        let data = match tokio::time::timeout(\n            std::time::Duration::from_secs(8),\n            xpc_client.recv_root(),\n        )\n        .await\n        {\n            Ok(Ok(data)) => {\n                tracing::error!("[SS-V17-RSD] ROOT_PASS");\n                data\n            }\n            Ok(Err(error)) => {\n                tracing::error!("[SS-V17-RSD] ROOT_FAIL error={error}");\n                return Err(error);\n            }\n            Err(_) => {\n                tracing::error!("[SS-V17-RSD] ROOT_TIMEOUT");\n                return Err(IdeviceError::InternalError(\n                    "RSD root response timed out".into(),\n                ));\n            }\n        };\n'''
    if text.count(old) != 1:
        die(f"stock RSD new() preamble anchor count={text.count(old)}")
    text = text.replace(old, new, 1)

    old_ok = '''        Ok(Self {\n            services,\n            protocol_version,\n            properties,\n            uuid,\n        })\n'''
    new_ok = '''        if services.is_empty() {\n            tracing::error!("[SS-V17-RSD] VALIDATION_FAIL reason=no_services");\n            return Err(IdeviceError::UnexpectedResponse(\n                "RSD handshake returned no services".into(),\n            ));\n        }\n        if uuid.is_empty() {\n            tracing::error!("[SS-V17-RSD] VALIDATION_FAIL reason=empty_uuid");\n            return Err(IdeviceError::UnexpectedResponse(\n                "RSD handshake returned an empty UUID".into(),\n            ));\n        }\n        tracing::error!(\n            "[SS-V17-RSD] HANDSHAKE_PASS services={} protocol={} uuid_present=true",\n            services.len(),\n            protocol_version\n        );\n\n        Ok(Self {\n            services,\n            protocol_version,\n            properties,\n            uuid,\n        })\n'''
    if text.count(old_ok) != 1:
        die(f"RSD success anchor count={text.count(old_ok)}")
    text = text.replace(old_ok, new_ok, 1)

    verify(text)
    p.write_text(text)
    print("v17 RSD core phased timeouts, validation, and runtime markers applied")


if __name__ == "__main__":
    main()
