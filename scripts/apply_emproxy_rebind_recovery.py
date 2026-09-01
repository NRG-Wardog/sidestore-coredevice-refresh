#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_emproxy_rebind_recovery.py <em_proxy/src/lib.rs>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[EMP-RECOVER] UDP socket rebound after ENOTCONN"

required = [
    "let mut socket = match std::net::UdpSocket::bind(bind_addr)",
    "std::io::ErrorKind::NotConnected",
    "raw_os_error() == Some(57)",
    "[EMP-RECOVER] ENOTCONN detected; rebinding UDP socket",
    marker,
    "[EMP-RECOVER] UDP rebind failed",
]

if marker in s:
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"EMP recovery marker present but patch incomplete: {missing}")
    print("EMProxy ENOTCONN rebind recovery already present and verified")
    raise SystemExit(0)

old_decl = "    let socket = match std::net::UdpSocket::bind(bind_addr) {\n"
new_decl = "    let mut socket = match std::net::UdpSocket::bind(bind_addr) {\n"
if s.count(old_decl) != 1:
    raise SystemExit(f"Could not locate unique EMProxy UDP socket declaration; count={s.count(old_decl)}")
s = s.replace(old_decl, new_decl, 1)

old_err = '''                    _ => {
                        log_msg(3, format!("[EMP-NONBLOCK] UDP recv error: {}", e));
                        std::thread::sleep(std::time::Duration::from_millis(10));
                    }
'''

new_err = '''                    _ => {
                        // iOS can invalidate a previously-bound loopback UDP socket while
                        // the VPN/network path is transitioning.  Once recv_from() starts
                        // returning ENOTCONN (57), continuing to poll the same descriptor
                        // only creates an infinite error loop and permanently breaks the
                        // WireGuard control path.  Drop that descriptor and bind a fresh
                        // socket to the exact original endpoint instead.
                        let is_not_connected =
                            e.kind() == std::io::ErrorKind::NotConnected || e.raw_os_error() == Some(57);
                        if is_not_connected {
                            log_msg(
                                2,
                                format!(
                                    "[EMP-RECOVER] ENOTCONN detected; rebinding UDP socket endpoint={}",
                                    bind_addr
                                ),
                            );

                            // The old descriptor must be closed before rebinding the same
                            // local port because EMProxy intentionally does not rely on
                            // SO_REUSEPORT/SO_REUSEADDR semantics.
                            drop(socket);

                            loop {
                                match std::net::UdpSocket::bind(bind_addr) {
                                    Ok(new_socket) => {
                                        if let Err(set_err) = new_socket.set_nonblocking(true) {
                                            log_msg(
                                                3,
                                                format!(
                                                    "[EMP-RECOVER] rebound socket nonblocking setup failed: {}",
                                                    set_err
                                                ),
                                            );
                                            std::thread::sleep(std::time::Duration::from_millis(100));
                                            continue;
                                        }
                                        socket = new_socket;
                                        ready = false;
                                        log_msg(
                                            1,
                                            format!(
                                                "[EMP-RECOVER] UDP socket rebound after ENOTCONN endpoint={}",
                                                bind_addr
                                            ),
                                        );
                                        // Avoid a hot loop if iOS is still transitioning.
                                        std::thread::sleep(std::time::Duration::from_millis(100));
                                        break;
                                    }
                                    Err(bind_err) => {
                                        log_msg(
                                            3,
                                            format!(
                                                "[EMP-RECOVER] UDP rebind failed endpoint={} error={}",
                                                bind_addr, bind_err
                                            ),
                                        );
                                        match rx.try_recv() {
                                            Ok(_) => {
                                                log_msg(1, "EMP instructed to die during UDP recovery".to_string());
                                                return;
                                            }
                                            Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                                                log_msg(1, "Handle destroyed during UDP recovery".to_string());
                                                return;
                                            }
                                            Err(std::sync::mpsc::TryRecvError::Empty) => {}
                                        }
                                        std::thread::sleep(std::time::Duration::from_millis(100));
                                    }
                                }
                            }
                        } else {
                            log_msg(3, format!("[EMP-NONBLOCK] UDP recv error: {}", e));
                            std::thread::sleep(std::time::Duration::from_millis(10));
                        }
                    }
'''

if s.count(old_err) != 1:
    raise SystemExit(f"Could not locate unique patched recv error block; count={s.count(old_err)}")
s = s.replace(old_err, new_err, 1)

p.write_text(s)
patched = p.read_text()
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"EMProxy ENOTCONN recovery verification failed; missing: {missing}")

# The old behavior must not survive as an unconditional ENOTCONN logging loop.
if 'e.raw_os_error() == Some(57)' not in patched:
    raise SystemExit("EMProxy recovery raw errno guard missing")

print("EMProxy ENOTCONN rebind recovery applied and verified")
