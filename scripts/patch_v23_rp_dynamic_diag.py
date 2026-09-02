#!/usr/bin/env python3
"""Instrument pinned IDevice raw-RPPairing with secret-free stage diagnostics."""
from __future__ import annotations
from pathlib import Path
import sys

MARK = "[SS-V23-RPDIAG]"
def die(msg): raise SystemExit(msg)
def once(text, old, new, label):
    n=text.count(old)
    if n != 1: die(f"{label}: expected 1 anchor, found {n}")
    return text.replace(old,new,1)

def verify(path: Path):
    s=path.read_text()
    req=[MARK,"fn v23_route_source","RP_CONTROL_CONNECT_START","RP_CONTROL_CONNECT_PASS","RP_PAIRING_START","RP_PAIRING_PASS","RP_CREATE_LISTENER_START","RP_CREATE_LISTENER_PASS","RP_DYNAMIC_ROUTE","RP_DYNAMIC_CONNECT_START","RP_DYNAMIC_CONNECT_PASS","RP_DYNAMIC_CONNECT_FAIL","RP_TLS_PSK_START","RP_TLS_PSK_PASS","RP_TUNNEL_INFO","RP_RSD_CONNECT_START","RP_RSD_CONNECT_PASS","RP_RSD_HANDSHAKE_START","RP_RSD_HANDSHAKE_PASS","RP_TOTAL_PASS"]
    miss=[x for x in req if x not in s]
    if miss: die(f"v23 RP diagnostics missing: {miss}")
    for snippet in ["rpc.create_tcp_listener().await","let mut tunnel_addr = connect_addr;","tunnel_addr.set_port(tunnel_port);","tokio::net::TcpStream::connect(tunnel_addr)","connect_tls_psk_tunnel_native(tunnel_stream, rpc.encryption_key())"]:
        if snippet not in s: die(f"canonical RPPairing contract changed: {snippet}")
    marker_lines='\n'.join(x for x in s.splitlines() if MARK in x)
    for secret in ["private_key=","public_key=","identifier=","HostPrivateKey","RootPrivateKey","DeviceCertificate","HostCertificate","UDID="]:
        if secret in marker_lines: die(f"sensitive marker content: {secret}")

def main():
    if len(sys.argv)!=2: die("usage: patch_v23_rp_dynamic_diag.py <idevice-root>")
    root=Path(sys.argv[1]); p=root/'ffi/src/tunnel_provider.rs'
    if not p.is_file(): die(f"missing {p}")
    s=p.read_text()
    if MARK in s:
        verify(p); print('v23 RP dynamic diagnostics already present and verified'); return

    anchor='struct PinCtx(*mut c_void);\n'
    helper=r'''fn v23_route_source(target: std::net::SocketAddr) -> String {
    let bind_addr = match target {
        std::net::SocketAddr::V4(_) => std::net::SocketAddr::new(std::net::IpAddr::V4(std::net::Ipv4Addr::UNSPECIFIED), 0),
        std::net::SocketAddr::V6(_) => std::net::SocketAddr::new(std::net::IpAddr::V6(std::net::Ipv6Addr::UNSPECIFIED), 0),
    };
    match std::net::UdpSocket::bind(bind_addr) {
        Ok(sock) => match sock.connect(target).and_then(|_| sock.local_addr()) {
            Ok(local) => local.to_string(),
            Err(error) => format!("route-probe-error:{error}"),
        },
        Err(error) => format!("route-probe-bind-error:{error}"),
    }
}

'''
    s=once(s,anchor,helper+anchor,'route-source helper')

    a=s.find('async fn finish_tunnel('); b=s.find('\nfn write_result(',a)
    if a<0 or b<0: die('finish_tunnel boundaries missing')
    finish=r'''async fn finish_tunnel(
    rpc: &mut idevice::remote_pairing::RemotePairingClient<impl idevice::remote_pairing::RpPairingSocketProvider>,
    connect_addr: std::net::SocketAddr,
) -> Result<(idevice::tcp::handle::AdapterHandle, RsdHandshake), IdeviceError> {
    use idevice::remote_pairing::connect_tls_psk_tunnel_native;
    let total_started = std::time::Instant::now();

    let listener_started = std::time::Instant::now();
    tracing::error!("[SS-V23-RPDIAG] RP_CREATE_LISTENER_START control_peer={connect_addr}");
    let tunnel_port = match rpc.create_tcp_listener().await {
        Ok(port) => {
            tracing::error!("[SS-V23-RPDIAG] RP_CREATE_LISTENER_PASS port={} elapsed_ms={}", port, listener_started.elapsed().as_millis());
            port
        }
        Err(error) => {
            tracing::error!("[SS-V23-RPDIAG] RP_CREATE_LISTENER_FAIL elapsed_ms={} error={error:?}", listener_started.elapsed().as_millis());
            return Err(error);
        }
    };

    let mut tunnel_addr = connect_addr;
    tunnel_addr.set_port(tunnel_port);
    let route_before = v23_route_source(tunnel_addr);
    tracing::error!("[SS-V23-RPDIAG] RP_DYNAMIC_ROUTE target={} route_source={} control_ip_same=true", tunnel_addr, route_before);

    let connect_started = std::time::Instant::now();
    tracing::error!("[SS-V23-RPDIAG] RP_DYNAMIC_CONNECT_START target={tunnel_addr}");
    let tunnel_stream = match run_global_timeout(|| tokio::net::TcpStream::connect(tunnel_addr)).await {
        Ok(stream) => {
            tracing::error!("[SS-V23-RPDIAG] RP_DYNAMIC_CONNECT_PASS target={} local={:?} peer={:?} elapsed_ms={}", tunnel_addr, stream.local_addr(), stream.peer_addr(), connect_started.elapsed().as_millis());
            stream
        }
        Err(error) => {
            let route_after = v23_route_source(tunnel_addr);
            tracing::error!("[SS-V23-RPDIAG] RP_DYNAMIC_CONNECT_FAIL target={} elapsed_ms={} route_before={} route_after={} error={error:?}", tunnel_addr, connect_started.elapsed().as_millis(), route_before, route_after);
            return Err(IdeviceError::InternalError(format!("RP dynamic TCP connect to {tunnel_addr}: {error}")));
        }
    };

    let tls_started = std::time::Instant::now();
    tracing::error!("[SS-V23-RPDIAG] RP_TLS_PSK_START target={} psk_len={}", tunnel_addr, rpc.encryption_key().len());
    let tunnel = match connect_tls_psk_tunnel_native(tunnel_stream, rpc.encryption_key()).await {
        Ok(tunnel) => {
            tracing::error!("[SS-V23-RPDIAG] RP_TLS_PSK_PASS elapsed_ms={}", tls_started.elapsed().as_millis());
            tunnel
        }
        Err(error) => {
            tracing::error!("[SS-V23-RPDIAG] RP_TLS_PSK_FAIL elapsed_ms={} error={error:?}", tls_started.elapsed().as_millis());
            return Err(error);
        }
    };

    let client_ip: std::net::IpAddr = tunnel.info.client_address.parse().map_err(|e| IdeviceError::InternalError(format!("{e}")))?;
    let server_ip: std::net::IpAddr = tunnel.info.server_address.parse().map_err(|e| IdeviceError::InternalError(format!("{e}")))?;
    let mtu=tunnel.info.mtu as usize; let rsd_port=tunnel.info.server_rsd_port;
    tracing::error!("[SS-V23-RPDIAG] RP_TUNNEL_INFO client_addr={} server_addr={} mtu={} rsd_port={}", client_ip, server_ip, mtu, rsd_port);

    let raw=tunnel.into_inner();
    let mut adapter=idevice::tcp::adapter::Adapter::new(Box::new(raw),client_ip,server_ip);
    adapter.set_mss(mtu.saturating_sub(60));
    let mut adapter=adapter.to_async_handle();

    let rsd_started=std::time::Instant::now();
    tracing::error!("[SS-V23-RPDIAG] RP_RSD_CONNECT_START port={rsd_port}");
    let rsd_stream=match adapter.connect(rsd_port).await {
        Ok(stream) => { tracing::error!("[SS-V23-RPDIAG] RP_RSD_CONNECT_PASS port={} elapsed_ms={}",rsd_port,rsd_started.elapsed().as_millis()); stream }
        Err(error) => { tracing::error!("[SS-V23-RPDIAG] RP_RSD_CONNECT_FAIL port={} elapsed_ms={} error={error}",rsd_port,rsd_started.elapsed().as_millis()); return Err(IdeviceError::InternalError(format!("{error}"))); }
    };
    let hs_started=std::time::Instant::now();
    tracing::error!("[SS-V23-RPDIAG] RP_RSD_HANDSHAKE_START");
    let handshake=match RsdHandshake::new(rsd_stream).await {
        Ok(h) => { tracing::error!("[SS-V23-RPDIAG] RP_RSD_HANDSHAKE_PASS elapsed_ms={}",hs_started.elapsed().as_millis()); h }
        Err(error) => { tracing::error!("[SS-V23-RPDIAG] RP_RSD_HANDSHAKE_FAIL elapsed_ms={} error={error:?}",hs_started.elapsed().as_millis()); return Err(error); }
    };
    tracing::error!("[SS-V23-RPDIAG] RP_TOTAL_PASS elapsed_ms={}",total_started.elapsed().as_millis());
    Ok((adapter,handshake))
}
'''
    s=s[:a]+finish+s[b:]

    a=s.find('#[unsafe(no_mangle)]\npub unsafe extern "C" fn tunnel_create_rppairing('); b=s.find('\n/// The peer device identity learned during a successful pair-setup.',a)
    if a<0 or b<0: die('tunnel_create_rppairing boundaries missing')
    body=s[a:b]
    old='''    let res = run_sync_local(async {\n        // Connect directly and use raw RPPairing protocol\n        let stream = run_global_timeout(|| tokio::net::TcpStream::connect(socket_addr))\n            .await\n            .map_err(|e| IdeviceError::InternalError(format!("connect: {e}")))?;\n        let conn = RpPairingSocket::new(stream);\n\n        let mut rpc = RemotePairingClient::new(conn, &host);\n        rpc.connect(rpf, async || get_pin(pin_callback, &ctx))\n            .await?;\n\n        finish_tunnel(&mut rpc, socket_addr).await\n    });\n'''
    new=r'''    let res = run_sync_local(async {
        let total_started=std::time::Instant::now();
        let route_source=v23_route_source(socket_addr);
        tracing::error!("[SS-V23-RPDIAG] RP_CONTROL_CONNECT_START target={} route_source={} paired_before={}",socket_addr,route_source,rpf.is_paired());
        let control_started=std::time::Instant::now();
        let stream=match run_global_timeout(|| tokio::net::TcpStream::connect(socket_addr)).await {
            Ok(stream) => { tracing::error!("[SS-V23-RPDIAG] RP_CONTROL_CONNECT_PASS target={} local={:?} peer={:?} elapsed_ms={}",socket_addr,stream.local_addr(),stream.peer_addr(),control_started.elapsed().as_millis()); stream }
            Err(error) => { tracing::error!("[SS-V23-RPDIAG] RP_CONTROL_CONNECT_FAIL target={} elapsed_ms={} error={error:?}",socket_addr,control_started.elapsed().as_millis()); return Err(IdeviceError::InternalError(format!("RP control connect: {error}"))); }
        };
        let conn=RpPairingSocket::new(stream);
        let pairing_started=std::time::Instant::now();
        tracing::error!("[SS-V23-RPDIAG] RP_PAIRING_START paired_before={}",rpf.is_paired());
        let mut rpc=RemotePairingClient::new(conn,&host);
        if let Err(error)=rpc.connect(rpf,async || get_pin(pin_callback,&ctx)).await {
            tracing::error!("[SS-V23-RPDIAG] RP_PAIRING_FAIL elapsed_ms={} error={error:?}",pairing_started.elapsed().as_millis());
            return Err(error);
        }
        tracing::error!("[SS-V23-RPDIAG] RP_PAIRING_PASS paired_after={} psk_len={} elapsed_ms={}",rpf.is_paired(),rpc.encryption_key().len(),pairing_started.elapsed().as_millis());
        let result=finish_tunnel(&mut rpc,socket_addr).await;
        if result.is_err() { tracing::error!("[SS-V23-RPDIAG] RP_TOTAL_FAIL elapsed_ms={} error={:?}",total_started.elapsed().as_millis(),result.as_ref().err()); }
        result
    });
'''
    if old not in body: die('rppairing async body anchor changed')
    body=body.replace(old,new,1); s=s[:a]+body+s[b:]
    p.write_text(s); verify(p)
    print('v23 RP dynamic-listener diagnostics applied and verified')

if __name__=='__main__': main()
