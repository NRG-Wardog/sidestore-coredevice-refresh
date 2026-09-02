# V22 route contract

`10.7.0.1:49152 -> RemotePairing -> create_tcp_listener -> 10.7.0.1:<dynamic> -> TLS-PSK -> CDTunnel -> RSD`

Fallback only:

`10.7.0.1:62078 -> QueryType -> StartSession -> StartService(CoreDeviceProxy) -> same peer:<service port> -> CDTunnel -> RSD`

The IDevice native build emits secret-free stage markers prefixed with `[SS-V22-RP]`. The Swift gateway emits `[SS-V22-ADAPT]`. The inherited, proven Lockdown protocol markers remain `[SS-V13-IDEVICE]`.
