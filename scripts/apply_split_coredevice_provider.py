#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit(
        "usage: apply_split_coredevice_provider.py "
        "<ffi/src/provider.rs> <idevice/src/services/core_device_proxy.rs>"
    )

provider_path = Path(sys.argv[1])
proxy_path = Path(sys.argv[2])
provider = provider_path.read_text()
proxy = proxy_path.read_text()
marker = "[SS-V11-SPLIT-PROVIDER]"

if marker in provider and "[SS-V11-COREDEVICE]" in proxy:
    required_provider = [
        "struct SideStoreSplitTcpProvider",
        "idevice_sidestore_split_tcp_provider_new",
        "SERVICE_CANDIDATE_START",
        "SERVICE_CONNECT_SUCCESS",
        "CONTROL_CONNECT_SUCCESS",
        "sidestore_service_candidates",
    ]
    required_proxy = [
        "[SS-V11-COREDEVICE] CDTUNNEL_START",
        "[SS-V11-COREDEVICE] CDTUNNEL_SUCCESS",
        "[SS-V11-COREDEVICE] CDTUNNEL_FAILED",
    ]
    missing = [x for x in required_provider if x not in provider]
    missing += [x for x in required_proxy if x not in proxy]
    if missing:
        raise SystemExit(f"v11 split provider marker present but patch incomplete: {missing}")
    print("v11 split Lockdown/CoreDevice provider already present and verified")
    raise SystemExit(0)

provider_anchor = "pub struct IdeviceProviderHandle(pub Box<dyn IdeviceProvider>);\n"
if provider_anchor not in provider:
    raise SystemExit("Could not locate IdeviceProviderHandle anchor")

provider_helper = r'''

// SideStore v11: split the transport used by IdeviceService::connect.
//
// Lockdown itself is reached through the proven LocalVPN reflection endpoint
// 10.7.0.1:62078. Lockdown then returns a dynamic service port. Sending that
// dynamic port back through the raw-packet NAT46 bridge is invalid for an IPv6
// link-local en0 destination, so service connections are made by the kernel
// directly against en0/loopback candidates. This preserves the trusted
// Lockdown session while completely removing RemotePairing createListener and
// NAT46 from the CoreDeviceProxy data path.
#[cfg(feature = "tcp")]
#[derive(Debug)]
struct SideStoreSplitTcpProvider {
    control_addr: std::net::IpAddr,
    control_scope_id: Option<u32>,
    pairing_file: idevice::pairing_file::PairingFile,
    label: String,
}

#[cfg(feature = "tcp")]
fn sidestore_scoped_addr(
    addr: std::net::IpAddr,
    scope_id: Option<u32>,
    port: u16,
) -> std::net::SocketAddr {
    match addr {
        std::net::IpAddr::V4(ip) => std::net::SocketAddr::V4(
            std::net::SocketAddrV4::new(ip, port),
        ),
        std::net::IpAddr::V6(ip) => std::net::SocketAddr::V6(
            std::net::SocketAddrV6::new(ip, port, 0, scope_id.unwrap_or(0)),
        ),
    }
}

#[cfg(all(feature = "tcp", unix))]
fn sidestore_en0_candidates(port: u16) -> Vec<(std::net::SocketAddr, String)> {
    let mut link_local_v6 = Vec::new();
    let mut global_v6 = Vec::new();
    let mut ipv4 = Vec::new();

    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) != 0 || head.is_null() {
            tracing::error!(
                "[SS-V11-SPLIT-PROVIDER] GETIFADDRS_FAILED error={}",
                std::io::Error::last_os_error()
            );
            return Vec::new();
        }

        let mut cursor = head;
        while !cursor.is_null() {
            let ifa = &*cursor;
            if !ifa.ifa_name.is_null() && !ifa.ifa_addr.is_null() {
                let name = std::ffi::CStr::from_ptr(ifa.ifa_name)
                    .to_string_lossy()
                    .into_owned();
                if name == "en0" {
                    let family = (*ifa.ifa_addr).sa_family as i32;
                    if family == libc::AF_INET6 {
                        let sin6 = &*(ifa.ifa_addr as *const libc::sockaddr_in6);
                        let ip = std::net::Ipv6Addr::from(sin6.sin6_addr.s6_addr);
                        if !ip.is_unspecified() && !ip.is_multicast() && !ip.is_loopback() {
                            let ifindex = if sin6.sin6_scope_id != 0 {
                                sin6.sin6_scope_id
                            } else {
                                libc::if_nametoindex(ifa.ifa_name)
                            };
                            let addr = std::net::SocketAddr::V6(
                                std::net::SocketAddrV6::new(ip, port, 0, ifindex),
                            );
                            if ip.is_unicast_link_local() {
                                link_local_v6.push((addr, "en0-v6-linklocal".to_string()));
                            } else {
                                global_v6.push((addr, "en0-v6-global".to_string()));
                            }
                        }
                    } else if family == libc::AF_INET {
                        let sin = &*(ifa.ifa_addr as *const libc::sockaddr_in);
                        let ip = std::net::Ipv4Addr::from(sin.sin_addr.s_addr.to_ne_bytes());
                        if !ip.is_unspecified() && !ip.is_loopback() {
                            ipv4.push((
                                std::net::SocketAddr::V4(std::net::SocketAddrV4::new(ip, port)),
                                "en0-v4".to_string(),
                            ));
                        }
                    }
                }
            }
            cursor = ifa.ifa_next;
        }
        libc::freeifaddrs(head);
    }

    link_local_v6.extend(global_v6);
    link_local_v6.extend(ipv4);
    link_local_v6
}

#[cfg(all(feature = "tcp", not(unix)))]
fn sidestore_en0_candidates(_port: u16) -> Vec<(std::net::SocketAddr, String)> {
    Vec::new()
}

#[cfg(feature = "tcp")]
fn sidestore_service_candidates(
    control_addr: std::net::IpAddr,
    control_scope_id: Option<u32>,
    port: u16,
) -> Vec<(std::net::SocketAddr, String)> {
    let mut candidates = sidestore_en0_candidates(port);
    candidates.push((
        std::net::SocketAddr::V6(std::net::SocketAddrV6::new(
            std::net::Ipv6Addr::LOCALHOST,
            port,
            0,
            0,
        )),
        "loopback-v6".to_string(),
    ));
    candidates.push((
        std::net::SocketAddr::V4(std::net::SocketAddrV4::new(
            std::net::Ipv4Addr::LOCALHOST,
            port,
        )),
        "loopback-v4".to_string(),
    ));
    candidates.push((
        sidestore_scoped_addr(control_addr, control_scope_id, port),
        "localvpn-last-resort".to_string(),
    ));

    let mut deduped = Vec::new();
    for candidate in candidates {
        if !deduped.iter().any(|(addr, _): &(std::net::SocketAddr, String)| *addr == candidate.0) {
            deduped.push(candidate);
        }
    }
    deduped
}

#[cfg(feature = "tcp")]
impl idevice::provider::IdeviceProvider for SideStoreSplitTcpProvider {
    fn connect(
        &self,
        port: u16,
    ) -> std::pin::Pin<
        Box<
            dyn std::future::Future<
                    Output = Result<idevice::Idevice, idevice::IdeviceError>,
                > + Send,
        >,
    > {
        let control_addr = self.control_addr;
        let control_scope_id = self.control_scope_id;
        let label = self.label.clone();

        Box::pin(async move {
            if port == 62078 {
                let target = sidestore_scoped_addr(control_addr, control_scope_id, port);
                tracing::error!(
                    "[SS-V11-SPLIT-PROVIDER] CONTROL_CONNECT_START target={}",
                    target
                );
                let stream = tokio::net::TcpStream::connect(target).await?;
                let local = stream.local_addr().ok();
                tracing::error!(
                    "[SS-V11-SPLIT-PROVIDER] CONTROL_CONNECT_SUCCESS local={:?} peer={}",
                    local,
                    target
                );
                return Ok(idevice::Idevice::new(Box::new(stream), label));
            }

            let candidates = sidestore_service_candidates(control_addr, control_scope_id, port);
            tracing::error!(
                "[SS-V11-SPLIT-PROVIDER] SERVICE_PLAN port={} count={} order={:?}",
                port,
                candidates.len(),
                candidates
                    .iter()
                    .map(|(addr, name)| format!("{}@{}", name, addr))
                    .collect::<Vec<_>>()
            );

            let mut last_error: Option<std::io::Error> = None;
            for (target, candidate_name) in candidates {
                tracing::error!(
                    "[SS-V11-SPLIT-PROVIDER] SERVICE_CANDIDATE_START name={} target={}",
                    candidate_name,
                    target
                );
                match tokio::time::timeout(
                    std::time::Duration::from_millis(1500),
                    tokio::net::TcpStream::connect(target),
                )
                .await
                {
                    Ok(Ok(stream)) => {
                        let local = stream.local_addr().ok();
                        tracing::error!(
                            "[SS-V11-SPLIT-PROVIDER] SERVICE_CONNECT_SUCCESS name={} local={:?} peer={}",
                            candidate_name,
                            local,
                            target
                        );
                        return Ok(idevice::Idevice::new(Box::new(stream), label));
                    }
                    Ok(Err(error)) => {
                        tracing::error!(
                            "[SS-V11-SPLIT-PROVIDER] SERVICE_CONNECT_FAILED name={} target={} kind={:?} error={}",
                            candidate_name,
                            target,
                            error.kind(),
                            error
                        );
                        last_error = Some(error);
                    }
                    Err(_) => {
                        let error = std::io::Error::new(
                            std::io::ErrorKind::TimedOut,
                            format!("service candidate timed out: {}", target),
                        );
                        tracing::error!(
                            "[SS-V11-SPLIT-PROVIDER] SERVICE_CONNECT_TIMEOUT name={} target={} timeout_ms=1500",
                            candidate_name,
                            target
                        );
                        last_error = Some(error);
                    }
                }
            }

            Err(last_error
                .unwrap_or_else(|| {
                    std::io::Error::new(
                        std::io::ErrorKind::NotFound,
                        "no CoreDeviceProxy service candidates",
                    )
                })
                .into())
        })
    }

    fn label(&self) -> &str {
        &self.label
    }

    fn get_pairing_file(
        &self,
    ) -> std::pin::Pin<
        Box<
            dyn std::future::Future<
                    Output = Result<idevice::pairing_file::PairingFile, idevice::IdeviceError>,
                > + Send,
        >,
    > {
        let pairing_file = self.pairing_file.clone();
        Box::pin(async move { Ok(pairing_file) })
    }
}

/// Creates SideStore's split provider: Lockdown control uses the supplied
/// LocalVPN address while Lockdown-started service ports use kernel-routed
/// en0/loopback candidates.
#[cfg(feature = "tcp")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn idevice_sidestore_split_tcp_provider_new(
    ip: *const idevice_sockaddr,
    pairing_file: *mut crate::pairing_file::IdevicePairingFile,
    label: *const std::os::raw::c_char,
    provider: *mut *mut IdeviceProviderHandle,
) -> *mut IdeviceFfiError {
    if ip.is_null() || pairing_file.is_null() || label.is_null() || provider.is_null() {
        return ffi_err!(idevice::IdeviceError::FfiInvalidArg);
    }

    let ip = ip as *const SockAddr;
    let (control_addr, control_scope_id) = match util::c_addr_to_rust(ip) {
        Ok(value) => value,
        Err(error) => return ffi_err!(error),
    };
    let label = match unsafe { std::ffi::CStr::from_ptr(label) }.to_str() {
        Ok(value) => value.to_string(),
        Err(_) => return ffi_err!(idevice::IdeviceError::FfiInvalidString),
    };

    // All fallible validation is complete. Consume the PairRecord exactly once.
    let pairing_file = unsafe { Box::from_raw(pairing_file) };
    let split = SideStoreSplitTcpProvider {
        control_addr,
        control_scope_id,
        pairing_file: pairing_file.0,
        label,
    };
    let boxed = Box::new(IdeviceProviderHandle(Box::new(split)));
    unsafe { *provider = Box::into_raw(boxed) };
    tracing::error!(
        "[SS-V11-SPLIT-PROVIDER] CREATE_SUCCESS control_addr={} control_scope={:?}",
        control_addr,
        control_scope_id
    );
    std::ptr::null_mut()
}
'''
provider = provider.replace(provider_anchor, provider_anchor + provider_helper, 1)

proxy_old = '''        let tunnel = CdTunnel::handshake(socket).await?;
        Ok(Self { tunnel })
'''
proxy_new = '''        tracing::error!("[SS-V11-COREDEVICE] CDTUNNEL_START transport=lockdown-started-service");
        let tunnel = match CdTunnel::handshake(socket).await {
            Ok(tunnel) => {
                tracing::error!("[SS-V11-COREDEVICE] CDTUNNEL_SUCCESS");
                tunnel
            }
            Err(error) => {
                tracing::error!("[SS-V11-COREDEVICE] CDTUNNEL_FAILED error={error:?}");
                return Err(error);
            }
        };
        Ok(Self { tunnel })
'''
if proxy_old not in proxy:
    raise SystemExit("Could not locate CoreDeviceProxy CDTunnel handshake")
proxy = proxy.replace(proxy_old, proxy_new, 1)

provider_path.write_text(provider)
proxy_path.write_text(proxy)

required_provider = [
    marker,
    "struct SideStoreSplitTcpProvider",
    "idevice_sidestore_split_tcp_provider_new",
    "SERVICE_PLAN",
    "SERVICE_CANDIDATE_START",
    "SERVICE_CONNECT_SUCCESS",
    "CONTROL_CONNECT_SUCCESS",
    "localvpn-last-resort",
]
required_proxy = [
    "[SS-V11-COREDEVICE] CDTUNNEL_START",
    "[SS-V11-COREDEVICE] CDTUNNEL_SUCCESS",
    "[SS-V11-COREDEVICE] CDTUNNEL_FAILED",
]
missing = [x for x in required_provider if x not in provider]
missing += [x for x in required_proxy if x not in proxy]
if missing:
    raise SystemExit(f"v11 split provider verification failed: {missing}")

print("v11 split Lockdown control/CoreDevice service provider applied and verified")
