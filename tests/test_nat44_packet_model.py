#!/usr/bin/env python3
'''Deterministic tests for IKEv2-first EMProxy NAT44 selection and tuples.'''

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import struct

VIRTUAL = ipaddress.IPv4Address("10.7.0.1").packed
CLIENT = ipaddress.IPv4Address("10.7.0.10").packed
IKEV2 = ipaddress.IPv4Address("10.31.2.206").packed
EN0 = ipaddress.IPv4Address("10.0.0.15").packed
CONTROL = 49152
LOCKDOWN = 62078
DYNAMIC_MIN = 49153


@dataclass(frozen=True)
class Interface:
    name: str
    ipv4: bytes
    index: int
    up: bool = True


def usable_ipv4(ip: bytes) -> bool:
    first, second, third, _ = ip
    return (
        ip != b"\0\0\0\0"
        and first != 127
        and not (first == 169 and second == 254)
        and not (first == 10 and second == 7 and third == 0)
        and first < 224
    )


def select_transit(interfaces: list[Interface]) -> Interface | None:
    candidates = [item for item in interfaces if item.up and usable_ipv4(item.ipv4)]
    ipsec = [item for item in candidates if item.name.startswith("ipsec")]
    en0 = [item for item in candidates if item.name == "en0"]
    fallback_en = [
        item for item in candidates if item.name.startswith("en") and item.name != "en0"
    ]
    for group in (ipsec, en0, fallback_en):
        if group:
            return max(group, key=lambda item: item.index)
    return None


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


def translate_forward(p: bytearray, transit: bytes) -> bytearray:
    out = bytearray(p)
    out[12:16] = VIRTUAL
    out[16:20] = transit
    repair(out)
    return out


def translate_reverse(p: bytearray) -> bytearray:
    out = bytearray(p)
    out[12:16] = VIRTUAL
    out[16:20] = CLIENT
    repair(out)
    return out


def test_selector() -> None:
    selected = select_transit(
        [
            Interface("en0", EN0, 11),
            Interface("ipsec7", IKEV2, 29),
            Interface("utun4", CLIENT, 31),
        ]
    )
    assert selected is not None
    assert selected.name == "ipsec7"
    assert selected.ipv4 == IKEV2

    selected = select_transit(
        [
            Interface("ipsec2", ipaddress.IPv4Address("10.31.1.10").packed, 20),
            Interface("ipsec9", ipaddress.IPv4Address("10.31.9.10").packed, 37),
            Interface("en0", EN0, 11),
        ]
    )
    assert selected is not None and selected.name == "ipsec9"

    selected = select_transit(
        [
            Interface("ipsec7", IKEV2, 29, up=False),
            Interface("en0", EN0, 11),
        ]
    )
    assert selected is not None and selected.name == "en0"

    selected = select_transit(
        [Interface("en2", ipaddress.IPv4Address("192.168.1.8").packed, 14)]
    )
    assert selected is not None and selected.name == "en2"

    selected = select_transit(
        [
            Interface("ipsec7", CLIENT, 29),
            Interface("en0", ipaddress.IPv4Address("169.254.4.3").packed, 11),
            Interface("lo0", ipaddress.IPv4Address("127.0.0.1").packed, 1),
        ]
    )
    assert selected is None


def test_packet_translation() -> None:
    client_port, service_port = 53551, 53008
    syn = packet(CLIENT, VIRTUAL, client_port, service_port, 0x02)
    assert eligible_forward(syn)

    forwarded = translate_forward(syn, IKEV2)
    assert forwarded[12:16] == VIRTUAL
    assert forwarded[16:20] == IKEV2
    assert struct.unpack("!HH", forwarded[20:24]) == (client_port, service_port)
    assert valid(forwarded)

    syn_ack = packet(IKEV2, VIRTUAL, service_port, client_port, 0x12)
    reversed_packet = translate_reverse(syn_ack)
    assert reversed_packet[12:16] == VIRTUAL
    assert reversed_packet[16:20] == CLIENT
    assert struct.unpack("!HH", reversed_packet[20:24]) == (service_port, client_port)
    assert valid(reversed_packet)

    assert not eligible_forward(packet(CLIENT, VIRTUAL, client_port, CONTROL, 0x02))
    assert not eligible_forward(packet(CLIENT, VIRTUAL, client_port, LOCKDOWN, 0x02))
    assert not eligible_forward(packet(CLIENT, VIRTUAL, client_port, service_port, 0x10))
    assert eligible_forward(
        packet(CLIENT, VIRTUAL, client_port, service_port, 0x10), has_flow=True
    )
    assert not eligible_forward(packet(VIRTUAL, CLIENT, CONTROL, client_port, 0x12))


def main() -> None:
    test_selector()
    test_packet_translation()
    print("IKEv2-first NAT44 model: selector and packet invariants passed")


if __name__ == "__main__":
    main()
