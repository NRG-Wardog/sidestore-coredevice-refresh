#!/usr/bin/env python3
'''Upgrade the v19 EMProxy NAT44 bridge to prefer an active IKEv2 IPv4.'''

from __future__ import annotations

from pathlib import Path
import sys

MARKER = "[EMP-TRANSIT] ipsec-first selector active"


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        die(f"{label}: expected exactly one source anchor, found {count}")
    return source.replace(old, new, 1)


def verify(source: str) -> None:
    required = [
        MARKER,
        "[EMP-TRANSIT] selected interface=",
        "struct Nat44TransitCandidate",
        "fn nat44_transit_ipv4",
        'name.starts_with("ipsec")',
        'name == "en0"',
        'name.starts_with("en")',
        'priority: "ikev2"',
        'priority: "fallback-en0"',
        'priority: "fallback-en"',
        "transit=ipsec-first,en0-fallback",
        "nat44_translate_forward",
        "nat44_translate_reverse",
        "TX-NAT44-FWD",
        "TX-NAT44-REV",
    ]
    missing = [item for item in required if item not in source]
    if missing:
        die(f"EMProxy IKEv2 transit verification failed; missing: {missing}")

    forbidden = [
        "fn nat44_en0_ipv4",
        "source=en0/en*",
        "physical=en0-ipv4",
        "payload_bytes",
        "hex::encode",
        "packet={:?}",
        "HostPrivateKey",
        "RootPrivateKey",
        "encryption_key=",
    ]
    leaked = [item for item in forbidden if item in source]
    if leaked:
        die(f"EMProxy IKEv2 transit barrier failed: {leaked}")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: apply_emproxy_ikev2_transit.py <em_proxy/src/lib.rs>")

    path = Path(sys.argv[1])
    if not path.exists():
        die(f"missing EMProxy source: {path}")

    source = path.read_text()
    if MARKER in source:
        verify(source)
        print("EMProxy IKEv2-first transit patch already present and verified")
        return

    for prerequisite in (
        "[EMP-NAT44] dynamic listener bridge active",
        "fn nat44_en0_ipv4",
        "nat44_translate_forward",
        "nat44_translate_reverse",
    ):
        if prerequisite not in source:
            die(f"EMProxy IKEv2 transit patch requires v19 prerequisite: {prerequisite}")

    old_selector = r'''fn nat44_en0_ipv4() -> Option<[u8; 4]> {
    #[cfg(unix)]
    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) != 0 || head.is_null() {
            log_msg(
                2,
                format!(
                    "[EMP-NAT44] getifaddrs failed error={}",
                    std::io::Error::last_os_error()
                ),
            );
            return None;
        }

        let mut en0: Option<[u8; 4]> = None;
        let mut fallback_en: Option<[u8; 4]> = None;
        let mut cursor = head;
        while !cursor.is_null() {
            let ifa = &*cursor;
            if !ifa.ifa_name.is_null() && !ifa.ifa_addr.is_null() {
                let family = (*ifa.ifa_addr).sa_family as i32;
                if family == libc::AF_INET {
                    let name = std::ffi::CStr::from_ptr(ifa.ifa_name).to_string_lossy();
                    let sin = &*(ifa.ifa_addr as *const libc::sockaddr_in);
                    // s_addr is stored in network byte order; to_ne_bytes()
                    // returns the bytes as laid out in sockaddr memory.
                    let ip = sin.sin_addr.s_addr.to_ne_bytes();
                    let unusable = ip[0] == 0
                        || ip[0] == 127
                        || (ip[0] == 169 && ip[1] == 254)
                        || (ip[0] == 10 && ip[1] == 7 && ip[2] == 0);
                    if !unusable {
                        if name == "en0" && en0.is_none() {
                            en0 = Some(ip);
                        } else if name.starts_with("en") && fallback_en.is_none() {
                            fallback_en = Some(ip);
                        }
                    }
                }
            }
            cursor = ifa.ifa_next;
        }
        libc::freeifaddrs(head);

        let selected = en0.or(fallback_en);
        if let Some(ip) = selected {
            log_msg(
                2,
                format!(
                    "[EMP-NAT44] selected physical IPv4={} source=en0/en*",
                    nat44_ipv4_text(ip)
                ),
            );
        }
        selected
    }

    #[cfg(not(unix))]
    {
        None
    }
}'''

    new_selector = r'''#[derive(Clone, Debug)]
struct Nat44TransitCandidate {
    interface: String,
    ipv4: [u8; 4],
    priority: &'static str,
    index: u32,
}

fn nat44_transit_ipv4() -> Option<[u8; 4]> {
    #[cfg(unix)]
    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) != 0 || head.is_null() {
            log_msg(
                2,
                format!(
                    "[EMP-TRANSIT] getifaddrs failed error={}",
                    std::io::Error::last_os_error()
                ),
            );
            return None;
        }

        log_msg(
            2,
            "[EMP-TRANSIT] ipsec-first selector active; fallback=en0/en*".to_string(),
        );
        let mut ipsec: Vec<Nat44TransitCandidate> = Vec::new();
        let mut en0: Vec<Nat44TransitCandidate> = Vec::new();
        let mut fallback_en: Vec<Nat44TransitCandidate> = Vec::new();
        let mut cursor = head;

        while !cursor.is_null() {
            let ifa = &*cursor;
            if !ifa.ifa_name.is_null() && !ifa.ifa_addr.is_null() {
                let family = (*ifa.ifa_addr).sa_family as i32;
                let flags = ifa.ifa_flags as i32;
                let is_up = flags & libc::IFF_UP != 0;

                if is_up && family == libc::AF_INET {
                    let name = std::ffi::CStr::from_ptr(ifa.ifa_name)
                        .to_string_lossy()
                        .into_owned();
                    let sin = &*(ifa.ifa_addr as *const libc::sockaddr_in);
                    // s_addr is stored in network byte order; to_ne_bytes()
                    // returns the octets as laid out in sockaddr memory.
                    let ip = sin.sin_addr.s_addr.to_ne_bytes();
                    let usable = ip != [0, 0, 0, 0]
                        && ip[0] != 127
                        && !(ip[0] == 169 && ip[1] == 254)
                        && !(ip[0] == 10 && ip[1] == 7 && ip[2] == 0)
                        && ip[0] < 224;

                    if usable {
                        let index = libc::if_nametoindex(ifa.ifa_name) as u32;
                        let candidate = if name.starts_with("ipsec") {
                            Nat44TransitCandidate {
                                interface: name,
                                ipv4: ip,
                                priority: "ikev2",
                                index,
                            }
                        } else if name == "en0" {
                            Nat44TransitCandidate {
                                interface: name,
                                ipv4: ip,
                                priority: "fallback-en0",
                                index,
                            }
                        } else if name.starts_with("en") {
                            Nat44TransitCandidate {
                                interface: name,
                                ipv4: ip,
                                priority: "fallback-en",
                                index,
                            }
                        } else {
                            cursor = ifa.ifa_next;
                            continue;
                        };

                        match candidate.priority {
                            "ikev2" => ipsec.push(candidate),
                            "fallback-en0" => en0.push(candidate),
                            _ => fallback_en.push(candidate),
                        }
                    }
                }
            }
            cursor = ifa.ifa_next;
        }
        libc::freeifaddrs(head);

        // Prefer the newest active IKEv2 interface. In the captured device
        // state this selects ipsec7/10.31.2.206 instead of en0/10.0.0.15.
        ipsec.sort_by_key(|candidate| candidate.index);
        en0.sort_by_key(|candidate| candidate.index);
        fallback_en.sort_by_key(|candidate| candidate.index);

        let selected = ipsec
            .pop()
            .or_else(|| en0.pop())
            .or_else(|| fallback_en.pop());

        if let Some(candidate) = selected {
            log_msg(
                2,
                format!(
                    "[EMP-TRANSIT] selected interface={} ipv4={} priority={}",
                    candidate.interface,
                    nat44_ipv4_text(candidate.ipv4),
                    candidate.priority
                ),
            );
            Some(candidate.ipv4)
        } else {
            log_msg(
                2,
                "[EMP-TRANSIT] no usable IPv4 interface; expected active ipsec* or en0/en*"
                    .to_string(),
            );
            None
        }
    }

    #[cfg(not(unix))]
    {
        None
    }
}'''

    source = replace_once(
        source,
        old_selector,
        new_selector,
        "v20 IKEv2-first selector",
    )
    source = replace_once(
        source,
        "let Some(physical_v4) = nat44_en0_ipv4() else {",
        "let Some(physical_v4) = nat44_transit_ipv4() else {",
        "v20 selector call",
    )
    source = replace_once(
        source,
        "[EMP-NAT44] forward blocked: no usable en0/en* IPv4 address",
        "[EMP-NAT44] forward blocked: no usable active ipsec*/en0/en* IPv4 address",
        "v20 no-transit diagnostic",
    )
    source = replace_once(
        source,
        "[EMP-NAT44] dynamic listener bridge active; virtual=10.7.0.1 physical=en0-ipv4 dynamic_min=49153 fixed_ports=49152,62078",
        "[EMP-NAT44] dynamic listener bridge active; virtual=10.7.0.1 transit=ipsec-first,en0-fallback dynamic_min=49153 fixed_ports=49152,62078",
        "v20 startup policy marker",
    )

    path.write_text(source)
    verify(path.read_text())
    print("EMProxy IKEv2-first transit selection applied and verified")


if __name__ == "__main__":
    main()
