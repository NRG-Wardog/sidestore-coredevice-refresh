#!/usr/bin/env python3
"""Harden v14 so every dynamic-listener candidate is actually attempted.

The first v14 matrix used a blocking socket2 connect inside async code. An
unreachable candidate could therefore block the Tokio worker and prevent the
outer timeout and all later routes from running. This patch makes the native
connect nonblocking, waits for writability with a per-connect deadline, checks
SO_ERROR, and bounds the complete PairVerify/listener/TLS/CDTunnel attempt for
each candidate. Every retry still creates a fresh RemotePairing session and a
fresh listener/key.
"""
from __future__ import annotations

from pathlib import Path
import sys

REMOTE_MARKER = "[SS-V14-CONNECT-ASYNC]"
ROUTE_TIMEOUT_MARKER = "CANDIDATE_TIMEOUT"


def fail(message: str) -> "NoReturn":
    raise SystemExit(message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        fail(f"{label}: expected exactly one source anchor, found {count}")
    return text.replace(old, new, 1)


def verify_remote(text: str) -> None:
    required = [
        REMOTE_MARKER,
        ROUTE_TIMEOUT_MARKER,
        "socket.set_nonblocking(true)?;",
        "stream.writable()",
        "stream.take_error()?",
        "Duration::from_millis(3000)",
        "Duration::from_secs(8)",
        "libc::EINPROGRESS",
        "fresh_pairverify_per_candidate=true",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        fail(f"v14 async-connect verification failed; missing: {missing}")
    if "socket.connect(&SockAddr::from(target))?;" in text:
        fail("v14 async-connect verification failed; blocking connect remains")


def verify_native(text: str) -> None:
    if "Duration::from_secs(180)" not in text:
        fail("v14 matrix FFI deadline was not raised to 180 seconds")
    if "Duration::from_secs(70)" in text:
        fail("legacy 70-second matrix deadline remains")


def main() -> None:
    if len(sys.argv) != 3:
        fail("usage: patch_v14_async_connect.py <remote_pairing/mod.rs> <ffi/tunnel_provider.rs>")

    remote_path = Path(sys.argv[1])
    native_path = Path(sys.argv[2])
    remote = remote_path.read_text()
    native = native_path.read_text()

    if REMOTE_MARKER not in remote:
        old_connect = '''async fn v14_connect(
    target: SocketAddr,
    bound_if: Option<u32>,
) -> Result<TcpStream, IdeviceError> {
    let domain = if target.is_ipv6() {
        Domain::IPV6
    } else {
        Domain::IPV4
    };
    let socket = Socket::new(domain, Type::STREAM, Some(Protocol::TCP))?;
    #[cfg(target_vendor = "apple")]
    if let Some(index) = bound_if {
        v14_set_bound_interface(&socket, target.is_ipv6(), index)?;
    }
    socket.connect(&SockAddr::from(target))?;
    let std_stream: std::net::TcpStream = socket.into();
    std_stream.set_nonblocking(true)?;
    Ok(TcpStream::from_std(std_stream)?)
}
'''
        new_connect = '''async fn v14_connect(
    target: SocketAddr,
    bound_if: Option<u32>,
) -> Result<TcpStream, IdeviceError> {
    let domain = if target.is_ipv6() {
        Domain::IPV6
    } else {
        Domain::IPV4
    };
    let socket = Socket::new(domain, Type::STREAM, Some(Protocol::TCP))?;
    socket.set_nonblocking(true)?;
    #[cfg(target_vendor = "apple")]
    if let Some(index) = bound_if {
        v14_set_bound_interface(&socket, target.is_ipv6(), index)?;
    }

    tracing::info!(
        "[SS-V14-CONNECT-ASYNC] START target={} ifindex={} timeout_ms=3000",
        target,
        bound_if.unwrap_or(0)
    );

    match socket.connect(&SockAddr::from(target)) {
        Ok(()) => {}
        Err(error)
            if matches!(
                error.raw_os_error(),
                Some(libc::EINPROGRESS) | Some(libc::EWOULDBLOCK) | Some(libc::EALREADY)
            ) => {}
        Err(error) => return Err(error.into()),
    }

    let std_stream: std::net::TcpStream = socket.into();
    let stream = TcpStream::from_std(std_stream)?;
    tokio::time::timeout(Duration::from_millis(3000), stream.writable())
        .await
        .map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                format!("v14 native connect timed out for {target}"),
            )
        })??;

    if let Some(error) = stream.take_error()? {
        return Err(error.into());
    }
    stream.set_nodelay(true)?;
    tracing::info!(
        "[SS-V14-CONNECT-ASYNC] SUCCESS target={} ifindex={}",
        target,
        bound_if.unwrap_or(0)
    );
    Ok(stream)
}
'''
        remote = replace_once(remote, old_connect, new_connect, "nonblocking native connect")

        old_loop = '''        match v14_attempt_route(
            pairing_file.clone(),
            hostname,
            control_addr,
            route.clone(),
            callback.clone(),
        )
        .await
        {
            Ok((adapter, handshake)) => {
                tracing::info!(
                    "[SS-V14-MATRIX] DYNAMIC_CONNECT_PASS index={} route={} target={}",
                    index,
                    route.label(),
                    route.target(control_addr)
                );
                tracing::info!("[SS-V14-MATRIX] RSD_PASS index={}", index);
                return Ok((adapter, handshake));
            }
            Err(error) => {
                tracing::warn!(
                    "[SS-V14-MATRIX] CANDIDATE_FAILED index={} route={} error={:?}",
                    index,
                    route.label(),
                    error
                );
                failures.push(format!("{}:{error:?}", route.label()));
            }
        }
'''
        new_loop = '''        let attempt = tokio::time::timeout(
            Duration::from_secs(8),
            v14_attempt_route(
                pairing_file.clone(),
                hostname,
                control_addr,
                route.clone(),
                callback.clone(),
            ),
        )
        .await;

        match attempt {
            Ok(Ok((adapter, handshake))) => {
                tracing::info!(
                    "[SS-V14-MATRIX] DYNAMIC_CONNECT_PASS index={} route={} target={}",
                    index,
                    route.label(),
                    route.target(control_addr)
                );
                tracing::info!("[SS-V14-MATRIX] RSD_PASS index={}", index);
                return Ok((adapter, handshake));
            }
            Ok(Err(error)) => {
                tracing::warn!(
                    "[SS-V14-MATRIX] CANDIDATE_FAILED index={} route={} error={:?}",
                    index,
                    route.label(),
                    error
                );
                failures.push(format!("{}:{error:?}", route.label()));
            }
            Err(_) => {
                tracing::warn!(
                    "[SS-V14-MATRIX] CANDIDATE_TIMEOUT index={} route={} target={} timeout_s=8",
                    index,
                    route.label(),
                    route.target(control_addr)
                );
                failures.push(format!("{}:timeout", route.label()));
            }
        }
'''
        remote = replace_once(remote, old_loop, new_loop, "per-candidate route deadline")

    if "Duration::from_secs(180)" not in native:
        native = replace_once(
            native,
            "std::time::Duration::from_secs(70),",
            "std::time::Duration::from_secs(180),",
            "matrix FFI deadline",
        )

    verify_remote(remote)
    verify_native(native)
    remote_path.write_text(remote)
    native_path.write_text(native)
    print("v14 async connect, per-candidate timeout, and matrix deadline verified")


if __name__ == "__main__":
    main()
