#!/usr/bin/env python3
"""Fail closed if the exact SideStore/idevice source no longer satisfies v12 assumptions."""

from pathlib import Path
import sys


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def require(path: Path, needles: list[str]) -> str:
    if not path.exists():
        die(f"missing upstream contract file: {path}")
    text = path.read_text()
    missing = [needle for needle in needles if needle not in text]
    if missing:
        die(f"upstream contract changed in {path}: missing {missing}")
    return text


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: verify_v12_upstream_contract.py <SideStore-idevice-root>")
    root = Path(sys.argv[1])

    provider_ffi = require(
        root / "ffi" / "src" / "provider.rs",
        [
            "pub unsafe extern \"C\" fn idevice_tcp_provider_new",
            "let pairing_file = unsafe { Box::from_raw(pairing_file) }",
            "let t = TcpProvider",
            "pairing_file: pairing_file.0",
        ],
    )
    if provider_ffi.find("Box::from_raw(pairing_file)") > provider_ffi.find("let t = TcpProvider"):
        die("provider ownership contract changed: PairingFile is not consumed before TcpProvider construction")

    provider = require(
        root / "idevice" / "src" / "provider.rs",
        [
            "pub struct TcpProvider",
            "pub pairing_file: PairingFile",
            "TcpStream::connect(socket_addr).await?",
            "let pairing_file = self.pairing_file.clone()",
        ],
    )
    if "SocketAddr::new(addr, port)" not in provider:
        die("TcpProvider no longer connects each requested service port on the configured IP")

    idevice_lib = require(
        root / "idevice" / "src" / "lib.rs",
        [
            "async fn connect(provider: &dyn IdeviceProvider)",
            "let mut lockdown = LockdownClient::connect(provider).await?",
            ".start_session(&provider.get_pairing_file().await?)",
            "lockdown.start_service(Self::service_name()).await?",
            "let mut idevice = provider.connect(port).await?",
        ],
    )
    order = [
        idevice_lib.find("LockdownClient::connect(provider).await?"),
        idevice_lib.find(".start_session(&provider.get_pairing_file().await?)"),
        idevice_lib.find("lockdown.start_service(Self::service_name()).await?"),
        idevice_lib.find("let mut idevice = provider.connect(port).await?"),
    ]
    if order != sorted(order) or any(index < 0 for index in order):
        die("IdeviceService connection order changed; v12 requires connect -> StartSession -> StartService -> service TCP")

    tunnel_ffi = require(
        root / "ffi" / "src" / "tunnel_provider.rs",
        [
            "pub unsafe extern \"C\" fn tunnel_create_usb",
            "let provider_ref: &dyn IdeviceProvider",
            "let proxy = CoreDeviceProxy::connect(provider_ref).await?",
            ".create_software_tunnel()",
            "let handshake = RsdHandshake::new(rsd_stream).await?",
            "pub unsafe extern \"C\" fn tunnel_create_rppairing",
            "tunnel_addr.set_port(tunnel_port)",
            "TcpStream::connect(tunnel_addr)",
        ],
    )
    if "lockdownd_pair" in tunnel_ffi.split("pub unsafe extern \"C\" fn tunnel_create_usb", 1)[1].split("pub unsafe extern \"C\" fn tunnel_pair_usb", 1)[0]:
        die("tunnel_create_usb unexpectedly performs pairing; v12 requires existing StartSession only")

    core_proxy = require(
        root / "idevice" / "src" / "services" / "core_device_proxy.rs",
        [
            'obf!("com.apple.internal.devicecompute.CoreDeviceProxy")',
            "let tunnel = CdTunnel::handshake(socket).await?",
            "pub fn create_software_tunnel",
        ],
    )
    if "pair" in core_proxy.lower().split("pub async fn new", 1)[1].split("pub fn tunnel_info", 1)[0]:
        die("CoreDeviceProxy handshake unexpectedly contains a pairing operation")

    pairing = require(
        root / "idevice" / "src" / "pairing_file.rs",
        [
            "#[derive(Serialize, Deserialize, Debug, Clone)]",
            "struct RawPairingFile",
            "plist::from_bytes::<RawPairingFile>(bytes)",
        ],
    )
    if "deny_unknown_fields" in pairing:
        die("PairingFile deserializer now rejects hybrid plists with extra RemotePairing keys")

    print("v12 upstream contract verified: existing Lockdown session, CoreDeviceProxy software tunnel, exact RP fallback")


if __name__ == "__main__":
    main()
