#!/usr/bin/env python3
"""Deterministic model tests for the EMProxy dynamic-listener NAT44 tuple."""

from __future__ import annotations

import ipaddress
import struct

VIRTUAL = ipaddress.IPv4Address("10.7.0.1").packed
CLIENT = ipaddress.IPv4Address("10.7.0.10").packed
PHYSICAL = ipaddress.IPv4Address("10.0.0.15").packed
CONTROL = 49152
LOCKDOWN = 62078
DYNAMIC_MIN = 49153


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    value = (~total) & 0xFFFF
    return value or 0xFFFF


def repair(packet: bytearray) -> None:
    ihl = (packet[0] & 0x0F) * 4
    total_len = struct.unpack("!H", packet[2:4])[0]
    packet[10:12] = b"\0\0"
    packet[10:12] = struct.pack("!H", checksum(bytes(packet[:ihl])))
    packet[ihl + 16 : ihl + 18] = b"\0\0"
    tcp = bytes(packet[ihl:total_len])
    pseudo = bytes(packet[12:20]) + b"\0\x06" + struct.pack("!H", len(tcp))
    packet[ihl + 16 : ihl + 18] = struct.pack("!H", checksum(pseudo + tcp))


def valid(packet: bytes) -> bool:
    ihl = (packet[0] & 0x0F) * 4
    total_len = struct.unpack("!H", packet[2:4])[0]
    if checksum(packet[:ihl]) != 0xFFFF:
        return False
    tcp = packet[ihl:total_len]
    pseudo = packet[12:20] + b"\0\x06" + struct.pack("!H", len(tcp))
    return checksum(pseudo + tcp) == 0xFFFF


def packet(src: bytes, dst: bytes, sport: int, dport: int, flags: int) -> bytearray:
    tcp = bytearray(20)
    tcp[0:4] = struct.pack("!HH", sport, dport)
    tcp[4:12] = struct.pack("!II", 100, 200)
    tcp[12] = 5 << 4
    tcp[13] = flags
    tcp[14:16] = struct.pack("!H", 65535)
    out = bytearray(40)
    out[0] = 0x45
    out[2:4] = struct.pack("!H", len(out))
    out[4:6] = b"\x12\x34"
    out[6:8] = struct.pack("!H", 0x4000)
    out[8] = 64
    out[9] = 6
    out[12:16] = src
    out[16:20] = dst
    out[20:] = tcp
    repair(out)
    assert valid(out)
    return out


def eligible_forward(p: bytes, has_flow: bool = False) -> bool:
    ihl = (p[0] & 0x0F) * 4
    src, dst = p[12:16], p[16:20]
    sport, dport = struct.unpack("!HH", p[ihl : ihl + 4])
    flags = p[ihl + 13]
    if not (src[:3] == b"\x0a\x07\x00" and src != VIRTUAL):
        return False
    if dst != VIRTUAL or dport < DYNAMIC_MIN or dport == LOCKDOWN:
        return False
    if sport in (CONTROL, LOCKDOWN):
        return False
    return has_flow or bool(flags & 0x02 and not flags & 0x10)


def translate_forward(p: bytearray) -> bytearray:
    out = bytearray(p)
    out[12:16] = VIRTUAL
    out[16:20] = PHYSICAL
    repair(out)
    return out


def translate_reverse(p: bytearray) -> bytearray:
    out = bytearray(p)
    out[12:16] = VIRTUAL
    out[16:20] = CLIENT
    repair(out)
    return out


def main() -> None:
    client_port, service_port = 53551, 53008
    syn = packet(CLIENT, VIRTUAL, client_port, service_port, 0x02)
    assert eligible_forward(syn)
    forwarded = translate_forward(syn)
    assert forwarded[12:16] == VIRTUAL
    assert forwarded[16:20] == PHYSICAL
    assert struct.unpack("!HH", forwarded[20:24]) == (client_port, service_port)
    assert valid(forwarded)

    syn_ack = packet(PHYSICAL, VIRTUAL, service_port, client_port, 0x12)
    reversed_packet = translate_reverse(syn_ack)
    assert reversed_packet[12:16] == VIRTUAL
    assert reversed_packet[16:20] == CLIENT
    assert struct.unpack("!HH", reversed_packet[20:24]) == (service_port, client_port)
    assert valid(reversed_packet)

    assert not eligible_forward(packet(CLIENT, VIRTUAL, client_port, CONTROL, 0x02))
    assert not eligible_forward(packet(CLIENT, VIRTUAL, client_port, LOCKDOWN, 0x02))
    assert not eligible_forward(packet(CLIENT, VIRTUAL, client_port, service_port, 0x10))
    assert eligible_forward(packet(CLIENT, VIRTUAL, client_port, service_port, 0x10), has_flow=True)
    assert not eligible_forward(packet(VIRTUAL, CLIENT, CONTROL, client_port, 0x12))

    print("NAT44 packet model: 10/10 assertions passed")


if __name__ == "__main__":
    main()
