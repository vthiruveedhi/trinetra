#!/usr/bin/env python3
"""scan_cameras.py — Find IP cameras on the local LAN.

Two passes:
1. TCP port sweep for RTSP (554) + common camera HTTP admin ports.
2. ONVIF WS-Discovery — most modern cameras respond to this multicast probe
   with their exact service URL.

Usage:
    python scan_cameras.py 192.168.68.0/24
    python scan_cameras.py --auto     # infer subnet from default gateway
"""
import argparse
import ipaddress
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

CAMERA_PORTS = [554, 80, 8080, 8000, 8001, 443, 8443, 8899, 8888, 88, 37777]

PORT_HINT = {
    554: "RTSP",
    80: "HTTP",
    443: "HTTPS",
    8080: "HTTP-alt",
    8000: "HTTP-alt",
    8001: "HTTP-alt",
    8443: "HTTPS-alt",
    88: "Foscam HTTP",
    8899: "cam admin",
    8888: "cam admin",
    37777: "Dahua",
}


def infer_subnet() -> str:
    try:
        out = subprocess.check_output(
            ["route", "-n", "get", "default"], text=True, timeout=2
        )
        iface = re.search(r"interface:\s*(\S+)", out).group(1)
        ip_out = subprocess.check_output(
            ["ipconfig", "getifaddr", iface], text=True, timeout=2
        ).strip()
        # Assume /24 — good enough for home networks
        return f"{'.'.join(ip_out.split('.')[:3])}.0/24"
    except Exception as e:
        print(f"[warn] could not infer subnet: {e}")
        sys.exit(1)


def check_ports(ip: str, timeout: float = 0.4) -> tuple[str, list[int]]:
    open_ports = []
    for port in CAMERA_PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            if s.connect_ex((ip, port)) == 0:
                open_ports.append(port)
        except OSError:
            pass
        finally:
            s.close()
    return (ip, open_ports)


def port_sweep(subnet: str) -> list[tuple[str, list[int]]]:
    net = ipaddress.ip_network(subnet, strict=False)
    hosts = [str(h) for h in net.hosts()]
    print(f"[sweep] {len(hosts)} hosts × {len(CAMERA_PORTS)} ports "
          f"(≈{len(hosts) * 0.4 / 100:.1f}s expected)")

    hits: list[tuple[str, list[int]]] = []
    with ThreadPoolExecutor(max_workers=100) as pool:
        futures = {pool.submit(check_ports, ip): ip for ip in hosts}
        for fut in as_completed(futures):
            ip, ports = fut.result()
            if ports:
                hits.append((ip, ports))
    return sorted(hits, key=lambda x: ipaddress.ip_address(x[0]))


def onvif_discover(timeout: float = 3.0) -> list[tuple[str, str]]:
    probe = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
        b' xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
        b' xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        b"<s:Header>"
        b'<a:Action s:mustUnderstand="1"'
        b' xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing">'
        b"http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>"
        b'<a:MessageID xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing">'
        b"uuid:12345678-1234-1234-1234-123456789012</a:MessageID>"
        b'<a:To s:mustUnderstand="1"'
        b' xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing">'
        b"urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>"
        b"</s:Header>"
        b"<s:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></s:Body>"
        b"</s:Envelope>"
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.settimeout(timeout)
    sock.sendto(probe, ("239.255.255.250", 3702))

    found: dict[str, str] = {}
    try:
        while True:
            data, addr = sock.recvfrom(65535)
            m = re.search(rb"<[^>]*XAddrs[^>]*>([^<]+)</", data)
            xaddrs = m.group(1).decode(errors="ignore") if m else ""
            found[addr[0]] = xaddrs
    except socket.timeout:
        pass
    sock.close()
    return sorted(found.items())


def annotate(ports: list[int]) -> str:
    return ", ".join(f"{p} ({PORT_HINT.get(p, '?')})" for p in ports)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("subnet", nargs="?",
                   help="CIDR to scan (e.g. 192.168.68.0/24)")
    p.add_argument("--auto", action="store_true",
                   help="Infer /24 subnet from default gateway (Mac only)")
    p.add_argument("--skip-onvif", action="store_true",
                   help="Skip ONVIF WS-Discovery multicast probe")
    p.add_argument("--skip-ports", action="store_true",
                   help="Skip TCP port sweep")
    args = p.parse_args()

    subnet = args.subnet
    if args.auto or not subnet:
        subnet = infer_subnet()
        print(f"[auto] inferred subnet: {subnet}")

    if not args.skip_ports:
        hits = port_sweep(subnet)
        print(f"\n=== Devices with camera-adjacent open ports ({len(hits)}) ===")
        for ip, ports in hits:
            marker = "  📷" if 554 in ports else "     "
            print(f"{marker} {ip:16s} → {annotate(ports)}")

    if not args.skip_onvif:
        print("\n=== ONVIF WS-Discovery (3s multicast wait) ===")
        onvif_hits = onvif_discover()
        if onvif_hits:
            for ip, xaddrs in onvif_hits:
                print(f"  📷 {ip:16s} → {xaddrs}")
        else:
            print("  (no ONVIF responses — cameras may be ONVIF-off or on another subnet)")


if __name__ == "__main__":
    main()
