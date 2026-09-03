#!/usr/bin/env python3
"""
SideStore V26: Local-Only CoreDevice Tunnel Patch
- Injects `allowLocalConnectionsOnly: true` into createListener request.
  This instructs Apple's remotepairingdeviced to bind a local-only listener
  for same-device connections, avoiding the physical en0 self-origin close_notify rejection.
- Expands candidate probing to prioritize dual loopback:
  1. loopback-v6: [::1]:<dynamic_port>
  2. loopback-v4: 127.0.0.1:<dynamic_port>
  3. peer-reflection: 10.7.0.1:<dynamic_port>
  4. local-utun-route-source: 10.7.1.1:<dynamic_port>
  5. default-route-local: en0 IPv4/IPv6
- Retains V25 exact 58-byte CDTunnel packet parity and decrypted TLS Alert decoder.
"""

from pathlib import Path
import sys

def die(msg: str) -> None:
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)

def once(content: str, old: str, new: str, desc: str) -> str:
    count = content.count(old)
    if count == 0:
        die(f"Pattern for {desc} not found")
    if count > 1:
        die(f"Pattern for {desc} found {count} times (expected 1)")
    return content.replace(old, new, 1)

def patch_remote_pairing_mod(root: Path) -> None:
    path = root / "idevice" / "src" / "remote_pairing" / "mod.rs"
    if not path.exists():
        die(f"remote_pairing/mod.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if "[SS-V26-RP] CREATE_LISTENER_LOCAL_ONLY_SENT" in text:
        print("v26 remote_pairing/mod.rs already patched")
        return

    # Check if V25 is present
    old_v25 = '''        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                        "peerConnectionsInfo": [
                            {
                                "owningPID": std::process::id() as u64,
                                "owningProcessName": "CoreDeviceService"
                            }
                        ],
                        "transportProtocolType": "tcp"
                    }
                }
            }
        });
        tracing::error!("[SS-V25-RP] CREATE_LISTENER_PEER_INFO_SENT owningProcess=CoreDeviceService");'''

    # If stock (before V25)
    old_stock = '''        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                        "transportProtocolType": "tcp"
                    }
                }
            }
        });'''

    new_req = '''        let request = plist!({
            "request": {
                "_0": {
                    "createListener": {
                        "key": base64::engine::general_purpose::STANDARD.encode(&self.encryption_key),
                        "peerConnectionsInfo": [
                            {
                                "owningPID": std::process::id() as u64,
                                "owningProcessName": "CoreDeviceService"
                            }
                        ],
                        "transportProtocolType": "tcp",
                        "allowLocalConnectionsOnly": true
                    }
                }
            }
        });
        tracing::error!("[SS-V26-RP] CREATE_LISTENER_LOCAL_ONLY_SENT allowLocalConnectionsOnly=true");'''

    if old_v25 in text:
        text = once(text, old_v25, new_req, "createListener allowLocalConnectionsOnly (from V25)")
    elif old_stock in text:
        text = once(text, old_stock, new_req, "createListener allowLocalConnectionsOnly (from stock)")
    else:
        die("Could not find createListener anchor in remote_pairing/mod.rs")

    path.write_text(text, encoding="utf-8")
    print("v26 remote_pairing/mod.rs patch applied")

def patch_tunnel_provider_candidates(root: Path) -> None:
    path = root / "ffi" / "src" / "tunnel_provider.rs"
    if not path.exists():
        die(f"tunnel_provider.rs not found at {path}")
    text = path.read_text(encoding="utf-8")
    if "[SS-V26-RPPROBE] V26_PROBE_PLAN" in text:
        print("v26 tunnel_provider.rs already patched")
        return

    # Replace candidate generation in tunnel_provider.rs
    old_candidates = '''    add_candidate("peer-reflection", connect_addr.ip());
    if let Ok(route_source) = v23_route_source(base_tunnel_addr).parse::<std::net::SocketAddr>() {
        add_candidate("local-utun-route-source", route_source.ip());
    }
    let loopback = match connect_addr {
        std::net::SocketAddr::V4(_) => std::net::IpAddr::V4(std::net::Ipv4Addr::LOCALHOST),
        std::net::SocketAddr::V6(_) => std::net::IpAddr::V6(std::net::Ipv6Addr::LOCALHOST),
    };
    add_candidate("loopback", loopback);
    if let Some(ip) = v24_default_route_source_ip(connect_addr.is_ipv6()) {
        add_candidate("default-route-local", ip);
    }

    tracing::error!("[SS-V24-RPPROBE] V24_PROBE_PLAN listener_port={} candidate_count={} candidates={:?}", tunnel_port, candidates.len(), candidates);'''

    new_candidates = '''    // V26: Prioritize local-only loopback sockets (IPv6 and IPv4) for allowLocalConnectionsOnly
    add_candidate("loopback-v6", std::net::IpAddr::V6(std::net::Ipv6Addr::LOCALHOST));
    add_candidate("loopback-v4", std::net::IpAddr::V4(std::net::Ipv4Addr::LOCALHOST));
    add_candidate("peer-reflection", connect_addr.ip());
    if let Ok(route_source) = v23_route_source(base_tunnel_addr).parse::<std::net::SocketAddr>() {
        add_candidate("local-utun-route-source", route_source.ip());
    }
    if let Some(ip) = v24_default_route_source_ip(false) {
        add_candidate("default-route-local-v4", ip);
    }
    if let Some(ip) = v24_default_route_source_ip(true) {
        add_candidate("default-route-local-v6", ip);
    }

    tracing::error!("[SS-V26-RPPROBE] V26_PROBE_PLAN listener_port={} candidate_count={} candidates={:?}", tunnel_port, candidates.len(), candidates);'''

    text = once(text, old_candidates, new_candidates, "V26 probe candidate generation")
    path.write_text(text, encoding="utf-8")
    print("v26 tunnel_provider.rs patch applied")

def main():
    if len(sys.argv) < 2:
        target = Path(".")
    else:
        target = Path(sys.argv[1])

    patch_remote_pairing_mod(target)
    patch_tunnel_provider_candidates(target)
    print("All V26 local-only tunnel patches applied successfully!")

if __name__ == "__main__":
    main()
