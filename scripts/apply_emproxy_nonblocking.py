#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_emproxy_nonblocking.py <em_proxy/src/lib.rs>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[EMP-NONBLOCK] UDP socket nonblocking mode enabled"

if marker in s:
    required = [
        "socket.set_nonblocking(true)",
        "[EMP-NONBLOCK] UDP socket nonblocking mode enabled",
        "[EMP-NONBLOCK] UDP recv error",
        "std::io::ErrorKind::WouldBlock",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"EMP nonblocking marker present but patch incomplete: {missing}")
    if "set_read_timeout(Some(std::time::Duration::from_millis(5)))" in s:
        raise SystemExit("EMP nonblocking marker present but legacy per-loop read timeout is still active")
    print("EMProxy nonblocking UDP fix already present and verified")
    raise SystemExit(0)

bind_old = '''    let socket = match std::net::UdpSocket::bind(bind_addr) {
        Ok(s) => s,
        Err(e) => {
            log_msg(3, format!("EMP socket bind error: {:?}", e));
            return Err(-4);
        }
    };
'''

bind_new = '''    let socket = match std::net::UdpSocket::bind(bind_addr) {
        Ok(s) => s,
        Err(e) => {
            log_msg(3, format!("EMP socket bind error: {:?}", e));
            return Err(-4);
        }
    };

    // Do not call SO_RCVTIMEO on every worker-loop iteration. On affected
    // runtimes the 5ms timeout maps to EINVAL, and the historical `continue`
    // turns that one socket error into an infinite CPU/logging loop. A single
    // nonblocking setup preserves the original requirement (wake frequently so
    // the stop channel can be checked) without relying on SO_RCVTIMEO.
    if let Err(e) = socket.set_nonblocking(true) {
        log_msg(
            3,
            format!("[EMP-NONBLOCK] failed to enable UDP nonblocking mode: {:?}", e),
        );
        return Err(-6);
    }
    log_msg(2, "[EMP-NONBLOCK] UDP socket nonblocking mode enabled".to_string());
'''

if bind_old not in s:
    raise SystemExit("Could not locate EMProxy UDP bind block")
s = s.replace(bind_old, bind_new, 1)

timeout_old = '''            // Attempt to read from the UDP socket
            match socket.set_read_timeout(Some(std::time::Duration::from_millis(5))) {
                Ok(_) => {}
                Err(e) => {
                    log_msg(2, format!("Unable to set UDP timeout: {:?}\\nRebinding to socket", e));
                    continue;
                }
            }
            let mut buf = [0_u8; 2048]; // we can use a small buffer because it will tell us if more is needed
'''

timeout_new = '''            // Attempt to read from the UDP socket. The socket is nonblocking,
            // so an idle receive returns WouldBlock and we yield briefly below.
            let mut buf = [0_u8; 2048]; // we can use a small buffer because it will tell us if more is needed
'''

if timeout_old not in s:
    raise SystemExit("Could not locate legacy per-loop UDP read-timeout block")
s = s.replace(timeout_old, timeout_new, 1)

recv_err_old = '''                Err(e) => match e.kind() {
                    std::io::ErrorKind::WouldBlock => {}
                    std::io::ErrorKind::TimedOut => {}
                    _ => {
                        log_msg(3, format!("Error receiving: {}", e));
                        std::thread::sleep(std::time::Duration::from_millis(10));
                        continue;
                    }
                },
'''

recv_err_new = '''                Err(e) => match e.kind() {
                    std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(std::time::Duration::from_millis(5));
                    }
                    // Retain TimedOut handling for portability if a platform or
                    // wrapper still surfaces it despite nonblocking mode.
                    std::io::ErrorKind::TimedOut => {
                        std::thread::sleep(std::time::Duration::from_millis(5));
                    }
                    _ => {
                        log_msg(3, format!("[EMP-NONBLOCK] UDP recv error: {}", e));
                        std::thread::sleep(std::time::Duration::from_millis(10));
                    }
                },
'''

if recv_err_old not in s:
    raise SystemExit("Could not locate EMProxy recv_from error handling block")
s = s.replace(recv_err_old, recv_err_new, 1)

# Keep public FFI documentation synchronized with the new startup failure code.
doc_old = "/// 0 on success, -1 if null address, -2 if UTF-8 error, -3 if invalid socket address, -4 if socket bind failed, -5 if crypto init failed"
doc_new = "/// 0 on success, -1 if null address, -2 if UTF-8 error, -3 if invalid socket address, -4 if socket bind failed, -5 if crypto init failed, -6 if UDP nonblocking setup failed"
if doc_old in s:
    s = s.replace(doc_old, doc_new, 1)

p.write_text(s)

patched = p.read_text()
required = [
    "socket.set_nonblocking(true)",
    "[EMP-NONBLOCK] UDP socket nonblocking mode enabled",
    "[EMP-NONBLOCK] UDP recv error",
    "std::thread::sleep(std::time::Duration::from_millis(5))",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"EMP nonblocking patch verification failed; missing: {missing}")

for forbidden in [
    "set_read_timeout(Some(std::time::Duration::from_millis(5)))",
    "Unable to set UDP timeout",
    "Rebinding to socket",
]:
    if forbidden in patched:
        raise SystemExit(f"EMP nonblocking patch verification failed; stale loop marker remains: {forbidden}")

print("EMProxy nonblocking UDP fix applied and verified")
