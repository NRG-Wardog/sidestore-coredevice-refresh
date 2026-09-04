#!/usr/bin/env python3
"""Patch the pinned jktcp checkout for long-running CoreDevice transfers."""

from pathlib import Path
import sys


def die(message: str) -> None:
    raise SystemExit(message)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_adapter(root: Path) -> None:
    path = root / "src" / "adapter.rs"
    text = path.read_text(encoding="utf-8")

    text = once(
        text,
        "const INITIAL_RTO_MS: u64 = 200;\n\nconst OUR_WSCALE: u8 = 8;",
        """const INITIAL_RTO_MS: u64 = 200;

/// Initial zero-window persist interval. It backs off to 6.4 seconds.
const INITIAL_PERSIST_MS: u64 = 200;

const OUR_WSCALE: u8 = 8;

#[cfg(target_os = "ios")]
unsafe extern "C" {
    fn lockdown_diag_rust_log(message: *const std::ffi::c_char);
}

#[cfg(target_os = "ios")]
fn transport_diag(message: &str) {
    if let Ok(message) = std::ffi::CString::new(message) {
        unsafe { lockdown_diag_rust_log(message.as_ptr()) };
    }
}

#[cfg(not(target_os = "ios"))]
fn transport_diag(_message: &str) {}""",
        "transport diagnostics and persist constants",
    )

    text = once(
        text,
        """    peer_window: u32,
    peer_wscale: Option<u8>,
}""",
        """    peer_window: u32,
    peer_wscale: Option<u8>,
    persist_sent_at: Option<Instant>,
    persist_retries: u32,
}""",
        "persist state fields",
    )
    text = once(
        text,
        """            peer_window: 0,
            peer_wscale: None,
        }""",
        """            peer_window: 0,
            peer_wscale: None,
            persist_sent_at: None,
            persist_retries: 0,
        }""",
        "persist state initialization",
    )

    text = once(
        text,
        """        self.check_retransmissions().await?;

        let host_ports: Vec<u16> = self.states.keys().cloned().collect();""",
        """        self.check_retransmissions().await?;
        self.check_zero_window_probes().await?;

        let host_ports: Vec<u16> = self.states.keys().cloned().collect();""",
        "persist check in write flush",
    )
    text = once(
        text,
        """                let chunk_len = chunk.len();
                if self.psh(chunk, hp).await.is_err() {
                    break;
                }
                if let Some(state) = self.states.get_mut(&hp) {""",
        """                let chunk_len = chunk.len();
                self.psh(chunk, hp).await?;
                if let Some(state) = self.states.get_mut(&hp) {""",
        "propagate outbound transport failures",
    )

    retransmit_anchor = """    /// Check every connection for timed-out unacked segments. Retransmit the
    /// oldest in-flight segment with exponential back-off; kill the connection"""
    persist_method = """    /// Probe a peer that advertised a zero receive window while application
    /// data remains queued. Without this, a lost window-update ACK leaves the
    /// stream blocked forever because there is no in-flight segment to retry.
    async fn check_zero_window_probes(&mut self) -> Result<(), std::io::Error> {
        struct Probe {
            host_port: u16,
            peer_port: u16,
            seq: u32,
            ack: u32,
            byte: u8,
        }

        let now = Instant::now();
        let mut probes = Vec::new();
        for state in self.states.values_mut() {
            let needs_probe = matches!(state.status, ConnectionStatus::Connected)
                && state.peer_window == 0
                && state.bytes_in_flight == 0
                && !state.write_buffer.is_empty();
            if !needs_probe {
                if state.peer_window > 0 || state.write_buffer.is_empty() {
                    state.persist_sent_at = None;
                    state.persist_retries = 0;
                }
                continue;
            }

            let interval = std::time::Duration::from_millis(
                INITIAL_PERSIST_MS << state.persist_retries.min(5),
            );
            let Some(sent_at) = state.persist_sent_at else {
                state.persist_sent_at = Some(now);
                transport_diag(&format!(
                    "[LOCKDOWN_DIAG] JKTCP_ZERO_WINDOW hp={} peer_port={} snd_nxt={} in_flight={} queued={}",
                    state.host_port,
                    state.peer_port,
                    state.seq,
                    state.bytes_in_flight,
                    state.write_buffer.len()
                ));
                continue;
            };
            if sent_at.elapsed() < interval {
                continue;
            }

            probes.push(Probe {
                host_port: state.host_port,
                peer_port: state.peer_port,
                seq: state.seq,
                ack: state.ack,
                byte: *state.write_buffer.front().unwrap(),
            });
            state.persist_sent_at = Some(now);
            state.persist_retries = state.persist_retries.saturating_add(1);
        }

        for probe in probes {
            let payload = [probe.byte];
            let tcp = TcpPacket::create(
                self.host_ip,
                self.peer_ip,
                probe.host_port,
                probe.peer_port,
                probe.seq,
                probe.ack,
                TcpFlags {
                    psh: true,
                    ack: true,
                    ..Default::default()
                },
                u16::MAX - 1,
                &[],
                &payload,
            );
            let ip = self.ip_wrap(&tcp);
            self.peer.write_all(&ip).await?;
            self.log_packet(&ip)?;
            transport_diag(&format!(
                "[LOCKDOWN_DIAG] JKTCP_ZERO_WINDOW_PROBE hp={} snd_nxt={}",
                probe.host_port, probe.seq
            ));
        }
        Ok(())
    }

"""
    text = once(text, retransmit_anchor, persist_method + retransmit_anchor, "persist method")

    text = once(
        text,
        """                Action::Kill => {
                    warn!("hp={hp} timed out after {MAX_RETRIES} retransmissions; closing");
                    if let Some(state) = self.states.get_mut(&hp) {
                        state.status = ConnectionStatus::Error(ErrorKind::TimedOut);
                    }
                }""",
        """                Action::Kill => {
                    warn!("hp={hp} timed out after {MAX_RETRIES} retransmissions; closing");
                    if let Some(state) = self.states.get_mut(&hp) {
                        transport_diag(&format!(
                            "[LOCKDOWN_DIAG] JKTCP_RETRANSMIT_TIMEOUT hp={} peer_port={} snd_nxt={} in_flight={} queued={}",
                            hp,
                            state.peer_port,
                            state.seq,
                            state.bytes_in_flight,
                            state.write_buffer.len()
                        ));
                        state.status = ConnectionStatus::Error(ErrorKind::TimedOut);
                    }
                }""",
        "retransmit timeout diagnostics",
    )

    window_anchor = """            // Update peer's advertised receive window
            if !(res.flags.syn) {
                let shift = state.peer_wscale.unwrap_or(0);
                state.peer_window = (res.window_size as u32) << shift;
            }"""
    window_replacement = """            // Update peer's advertised receive window.
            if !(res.flags.syn) {
                let old_window = state.peer_window;
                let shift = state.peer_wscale.unwrap_or(0);
                state.peer_window = (res.window_size as u32) << shift;
                if old_window > 0 && state.peer_window == 0 {
                    transport_diag(&format!(
                        "[LOCKDOWN_DIAG] JKTCP_ZERO_WINDOW_ADVERTISED hp={} peer_port={} snd_una={} snd_nxt={} in_flight={} queued={}",
                        state.host_port,
                        state.peer_port,
                        res.acknowledgment_number,
                        state.seq,
                        state.bytes_in_flight,
                        state.write_buffer.len()
                    ));
                } else if old_window == 0 && state.peer_window > 0 && state.persist_sent_at.is_some() {
                    transport_diag(&format!(
                        "[LOCKDOWN_DIAG] JKTCP_WINDOW_REOPENED hp={} peer_port={} window={} snd_una={} snd_nxt={} in_flight={} queued={}",
                        state.host_port,
                        state.peer_port,
                        state.peer_window,
                        res.acknowledgment_number,
                        state.seq,
                        state.bytes_in_flight,
                        state.write_buffer.len()
                    ));
                    state.persist_sent_at = None;
                    state.persist_retries = 0;
                }
            }"""
    text = once(text, window_anchor, window_replacement, "peer window diagnostics")

    test_anchor = """    /// After the RTO elapses without an ACK, the same segment is retransmitted
    /// with the same sequence number."""
    persist_test = """    #[tokio::test]
    async fn zero_window_persist_probe_recovers_lost_window_update() {
        tokio::time::pause();

        let (adapter_end, test_end) = tokio::io::duplex(1 << 16);
        let (mut test_rx, mut test_tx) = tokio::io::split(test_end);
        let mut adapter = Adapter::new(
            Box::new(TestTransport(adapter_end)),
            IpAddr::V6(HOST_IP),
            IpAddr::V6(PEER_IP),
        );
        let hp = handshake(&mut adapter, &mut test_rx, &mut test_tx).await;
        let original_seq = adapter.states[&hp].seq;

        adapter.states.get_mut(&hp).unwrap().peer_window = 0;
        adapter.queue_send(b"abc", hp).unwrap();
        adapter.write_buffer_flush().await.unwrap();
        assert!(adapter.states[&hp].persist_sent_at.is_some());

        tokio::time::advance(Duration::from_millis(INITIAL_PERSIST_MS + 1)).await;
        adapter.write_buffer_flush().await.unwrap();
        let probe = read_pkt(&mut test_rx).await;
        assert_eq!(probe.payload, b"a");
        assert_eq!(probe.sequence_number, original_seq);
        assert_eq!(adapter.states[&hp].seq, original_seq);
        assert_eq!(adapter.states[&hp].write_buffer.len(), 3);
        assert!(adapter.states[&hp].unacked.is_empty());

        let reopen = TcpPacket::create(
            IpAddr::V6(PEER_IP),
            IpAddr::V6(HOST_IP),
            PEER_PORT,
            hp,
            PEER_ISN + 1,
            original_seq,
            TcpFlags {
                ack: true,
                ..Default::default()
            },
            16,
            &[],
            &[],
        );
        adapter.process_tcp_packet_from_payload(&reopen).await.unwrap();
        adapter.write_buffer_flush().await.unwrap();
        let resumed = read_pkt(&mut test_rx).await;
        assert_eq!(resumed.payload, b"abc");
        assert!(adapter.states[&hp].persist_sent_at.is_none());
    }

"""
    text = once(text, test_anchor, persist_test + test_anchor, "zero-window persist test")

    path.write_text(text, encoding="utf-8")


def patch_handle(root: Path) -> None:
    path = root / "src" / "handle.rs"
    text = path.read_text(encoding="utf-8")

    old = """                    _ = crate::time::sleep(std::time::Duration::from_millis(250)), if has_pending_work => {
                        let _ = adapter.write_buffer_flush().await;
                    }"""
    new = """                    _ = crate::time::sleep(std::time::Duration::from_millis(250)), if has_pending_work => {
                        if let Err(e) = adapter.write_buffer_flush().await {
                            for (hp, tx) in handles.drain() {
                                let _ = tx.send(Err(e.kind().into()));
                                let _ = adapter.close(hp).await;
                            }
                            break;
                        }

                        // A retransmission timeout changes connection state without
                        // requiring another inbound packet. Wake blocked readers here.
                        let mut timed_out = Vec::new();
                        for (&hp, tx) in &handles {
                            if let Ok(ConnectionStatus::Error(kind)) = adapter.get_status(hp) {
                                if kind != std::io::ErrorKind::UnexpectedEof {
                                    let _ = tx.send(Err(std::io::Error::from(kind)));
                                }
                                timed_out.push(hp);
                            }
                        }
                        for hp in timed_out {
                            handles.remove(&hp);
                            let _ = adapter.close(hp).await;
                        }
                    }"""
    text = once(text, old, new, "wake readers after timer-driven failure")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_jktcp_reliability.py <jktcp-root>")
    root = Path(sys.argv[1]).resolve()
    if not (root / "Cargo.toml").is_file():
        die(f"not a jktcp checkout: {root}")
    patch_adapter(root)
    patch_handle(root)
    print("patched jktcp zero-window recovery and error propagation")


if __name__ == "__main__":
    main()
