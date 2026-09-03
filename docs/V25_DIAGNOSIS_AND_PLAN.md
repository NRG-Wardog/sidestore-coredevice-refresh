# SideStore V25: Exact Protocol Parity, Strict Cryptographic Verification, and Complete Alert Decoding

## 1. Executive Summary & Root-Cause Empirical Proof

Across V13–V24, SideStore's on-device self-refresh has been systematically isolated. In V24:
- LocalDevVPN (`10.7.1.1/32`, peer `10.7.0.1/32`) established reliably.
- RemotePairing control on `10.7.0.1:49152` completed `PairVerify` in 5–8 ms.
- `createListener` succeeded, returning dynamic TCP port (e.g. `60110`).
- Multi-candidate probing evaluated:
  - Candidate 0 (`peer-reflection 10.7.0.1:<port>`): timed out (IPv4 packet sent to IPv6-only listener on `utun4`).
  - Candidate 1 (`local-utun-route-source 10.7.1.1:<port>`): timed out.
  - Candidate 2 (`loopback 127.0.0.1:<port>`): rejected (`ECONNREFUSED` by Darwin kernel; loopback excluded by `remotepairingdeviced`).
  - Candidate 3 (`default-route-local 10.0.0.15:<port>`): **connected in 0-1 ms**.
- On Candidate 3:
  - TLS 1.2 PSK Handshake succeeded (`AES_256_CBC_SHA384`).
  - The first application data packet (`CDTunnel clientHandshakeRequest`) was transmitted.
  - The device immediately responded with TLS Alert `ct=21` and closed the connection.

### Empirical Proof on the Physical Device (iOS 26.6.1, build 23G83)
1. **RSD End-to-End Success**:
   Using `pymobiledevice3`'s `UserspaceRsdTunnel`, we established a complete, root-free CoreDevice tunnel over USBMux, executed the CDTunnel handshake, and queried RSD services. RSD returned all 40+ system developer services including `com.apple.mobile.installation_proxy.shim.remote` and `com.apple.streaming_zip_conduit.shim.remote` for UDID `00008101-001D29013461001E` on OS `26.6.1`. **This proves the iOS 26.6.1 device daemon stack is fully capable of running the CoreDevice tunnel and RSD.**

2. **Byte-for-Byte Protocol Parity Discrepancies**:
   - **CDTunnel Request Framing**:
     - *Known-good (`pymobiledevice3`)*:
       `CDTUNNEL_MAGIC` (8 bytes: `43 44 54 75 6e 6e 65 6c`) +
       Length `0x00 0x30` (48 bytes, 2-byte big-endian `u16`) +
       Body `{"type": "clientHandshakeRequest", "mtu": 16000}` (48 bytes, contains standard json formatting spaces). Total: **58 bytes**.
     - *SideStore V24/stock*:
       `serde_json::to_vec` produced `{"type":"clientHandshakeRequest","mtu":16000}` (45 bytes, no spaces). Total: **55 bytes**.
   - **`createListener` Request Metadata**:
     - *Known-good (`pymobiledevice3`)*:
       Includes `peerConnectionsInfo: [{"owningPID": <pid>, "owningProcessName": "CoreDeviceService"}]`.
       Live device syslog confirms that when this is present, `remotepairingdeviced` logs:
       `Starting a tunnel listener due to a request from :Optional("[CoreDeviceService[...]]")`.
     - *SideStore V24/stock*:
       Did not send `peerConnectionsInfo`. Device logged: `Starting a tunnel listener due to a request from :nil`.

3. **TLS Alert Obfuscation in V24**:
   In stock `idevice/src/remote_pairing/tls_psk.rs`, `read_app_data()` checked only `if ct != CT_APPLICATION_DATA { return Err(... got ct=21) }`. It never decrypted the alert, leaving `ct=21` as an opaque failure without disclosing level, description, or alert reason.

4. **Cryptographic Verification Laxity in Stock**:
   Stock `tls_psk.rs` logged `"Server Finished verify_data mismatch (continuing anyway)"` on corrupted or altered handshake verification.

---

## 2. V25 Implementation Scope

V25 implements complete protocol parity and diagnostic transparency in `scripts/patch_v25_parity_and_alert_diag.py`:

1. **Exact 58-Byte CDTunnel Packet Parity**:
   Sends the exact single-record application-data frame matching `pymobiledevice3`:
   - Magic: `b"CDTunnel"` (8 bytes)
   - Length: `(48u16).to_be_bytes()` (`0x00 0x30`)
   - Body: `br#"{"type": "clientHandshakeRequest", "mtu": 16000}"#` (48 bytes)
   - Emits `[SS-V25-CDT] HANDSHAKE_START tx_mode=single_tls_record body_len=48 frame_len=58`.

2. **Fragmentation-Safe Response Accumulation**:
   - Accumulates incoming TLS application-data records into a streaming buffer until full `expected_total` (`10 + body_len`) is received.
   - Preserves any extra decrypted plaintext using `prepend_read_data(&extra)` for subsequent smoltcp reads.
   - Emits `[SS-V25-CDT] RESPONSE_HEADER_PASS` and `[SS-V25-CDT] HANDSHAKE_PASS`.

3. **Comprehensive TLS Alert Decryption and Human-Readable Decoding**:
   - When `ct == CT_ALERT` (21) is encountered in `read_app_data`:
     - Decrypts the record using active session keys.
     - Parses `level` (`1 = warning`, `2 = fatal`).
     - Parses `description` and maps to standard RFC 5246 names (`0 = close_notify`, `10 = unexpected_message`, `40 = handshake_failure`, `49 = access_denied`, `70 = protocol_version`, `80 = internal_error`, etc.).
     - Emits `[SS-V25-ALERT] TLS_ALERT stage=after_cdtunnel_request level={level} level_name={level_name} description={description} description_name={desc_name} plaintext_len={len}`.
     - Returns a descriptive, human-readable error.

4. **Strict Server Finished Cryptographic Verification**:
   - Verifies `server_finished` `verify_data` against PRF master secret and handshake digest.
   - Emits `[SS-V25-TLS] SERVER_FINISHED_PASS` on match.
   - On mismatch: emits `[SS-V25-TLS] SERVER_FINISHED_FATAL verify_data mismatch` and immediately terminates the connection.

5. **`peerConnectionsInfo` Injection in `createListener`**:
   - Injects `peerConnectionsInfo: [{"owningPID": <pid>, "owningProcessName": "CoreDeviceService"}]`.
   - Emits `[SS-V25-RP] CREATE_LISTENER_PEER_INFO_SENT owningProcess=CoreDeviceService`.

6. **Preservation of All Prior Proven Architectural Fixes**:
   - V24 dynamic listener candidate probing (`peer-reflection`, `local-utun-route-source`, `default-route-local`, `loopback`).
   - V23 tunnel serialization lock (`TUNNEL_LOCK_WAIT` / `TUNNEL_LOCK_ACQUIRED`).
   - Nil-UDID transport failure handling in `AppBootManager.swift`.
   - V13 one-write Lockdown frames, `TCP_NODELAY`, and `QueryType` before `StartSession`.
