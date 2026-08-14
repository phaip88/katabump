#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
proxy_handler.py -- Parse PROXY_URL and generate sing-box config.json

Supported protocols:
  socks5://[user:pass@]host:port
  http://[user:pass@]host:port
  https://[user:pass@]host:port
  vless://uuid@host:port?security=tls&type=ws&...#name
  vmess://base64EncodedJSON
  hy2://password@host:port?sni=xxx&insecure=1
  hysteria2://password@host:port?sni=xxx
  tuic://uuid:password@host:port?sni=xxx&alpn=h3&congestion_control=bbr

Output: config.json with HTTP inbound on 127.0.0.1:8080
"""

import os
import sys
import json
import base64
from urllib.parse import urlparse, parse_qs, parse_qsl, urlencode, unquote

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8080


# ============================================================
# Protocol Parsers
# ============================================================

def parse_socks5(parsed):
    outbound = {
        "type": "socks",
        "tag": "proxy",
        "server": parsed.hostname,
        "server_port": parsed.port or 1080,
        "version": "5",
    }
    if parsed.username:
        outbound["username"] = unquote(parsed.username)
    if parsed.password:
        outbound["password"] = unquote(parsed.password)
    return outbound


def parse_http(parsed):
    outbound = {
        "type": "http",
        "tag": "proxy",
        "server": parsed.hostname,
        "server_port": parsed.port or 8080,
    }
    if parsed.username:
        outbound["username"] = unquote(parsed.username)
    if parsed.password:
        outbound["password"] = unquote(parsed.password)
    if parsed.scheme == "https":
        outbound["tls"] = {"enabled": True}
    return outbound


def get_param(params, name, default=""):
    """Return a query parameter with case-insensitive key compatibility."""
    wanted = name.lower()
    for key, values in params.items():
        if key.lower() == wanted and values:
            return values[0]
    return default


def normalize_ws_path_and_early_data(raw_path, params):
    """Normalize a WebSocket path and extract Xray-style early-data options.

    Supported share-link forms:
      path=/ws?ed=2560
      path=/ws&ed=2560 (seen in some exported links after decoding)
      path=/ws&ed=2560 as an encoded path value
      path=/ws&ed=2560 plus a top-level ed=2560 parameter
    """
    path = unquote(raw_path or "").strip() or "/"
    if not path.startswith("/"):
        path = "/" + path

    early_data = get_param(params, "ed", "").strip()
    header_name = (
        get_param(params, "eh", "")
        or get_param(params, "early_data_header_name", "")
        or "Sec-WebSocket-Protocol"
    ).strip()

    if "?" in path:
        base_path, raw_query = path.split("?", 1)
        remaining = []
        for key, value in parse_qsl(raw_query, keep_blank_values=True):
            if key.lower() == "ed" and not early_data:
                early_data = value
            else:
                remaining.append((key, value))
        path = base_path or "/"
        if remaining:
            path += "?" + urlencode(remaining, doseq=True)

    # Compatibility with exports that append '&ed=N' without a '?' inside path.
    legacy_parts = path.rsplit("&ed=", 1)
    if len(legacy_parts) == 2 and legacy_parts[1].isdigit():
        path = legacy_parts[0] or "/"
        if not early_data:
            early_data = legacy_parts[1]

    max_early_data = 0
    if early_data:
        try:
            max_early_data = int(early_data)
            if not 0 <= max_early_data <= 65535:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"Invalid VLESS WebSocket early data value: {early_data!r}") from exc

    return path, max_early_data, header_name


def parse_vless(parsed, params):
    outbound = {
        "type": "vless",
        "tag": "proxy",
        "server": parsed.hostname,
        "server_port": parsed.port or 443,
        "uuid": parsed.username,
    }

    # Flow (e.g. xtls-rprx-vision)
    flow = params.get("flow", [""])[0]
    if flow:
        outbound["flow"] = flow

    # TLS / REALITY
    security = params.get("security", [""])[0]
    if security in ("tls", "reality"):
        tls = {"enabled": True}

        sni = params.get("sni", [""])[0]
        if sni:
            tls["server_name"] = sni

        fp = params.get("fp", [""])[0]
        if fp:
            tls["utls"] = {"enabled": True, "fingerprint": fp}

        alpn = params.get("alpn", [""])[0]
        if alpn:
            tls["alpn"] = alpn.split(",")

        insecure = params.get("insecure", params.get("allowInsecure", ["0"]))[0]
        if insecure == "1":
            tls["insecure"] = True

        if security == "reality":
            reality = {"enabled": True}
            pbk = params.get("pbk", [""])[0]
            if pbk:
                reality["public_key"] = pbk
            sid = params.get("sid", [""])[0]
            if sid:
                reality["short_id"] = sid
            tls["reality"] = reality

        outbound["tls"] = tls

    # Transport
    net_type = get_param(params, "type", "").lower()
    if net_type == "ws":
        raw_path = get_param(params, "path", "/")
        path, max_early_data, early_data_header_name = normalize_ws_path_and_early_data(
            raw_path, params
        )
        transport = {"type": "ws", "path": path}

        host = get_param(params, "host", "").strip()
        if host:
            transport["headers"] = {"Host": host}

        if max_early_data:
            transport["max_early_data"] = max_early_data
            transport["early_data_header_name"] = early_data_header_name

        outbound["transport"] = transport
    elif net_type == "grpc":
        transport = {"type": "grpc"}
        sn = params.get("serviceName", [""])[0]
        if sn:
            transport["service_name"] = sn
        outbound["transport"] = transport
    elif net_type in ("http", "h2"):
        transport = {"type": "http"}
        path = params.get("path", [""])[0]
        if path:
            transport["path"] = unquote(path)
        host = params.get("host", [""])[0]
        if host:
            transport["host"] = [host]
        outbound["transport"] = transport

    return outbound


def parse_vmess(url_str):
    encoded = url_str[len("vmess://"):]
    # Fix base64 padding
    pad = 4 - len(encoded) % 4
    if pad != 4:
        encoded += "=" * pad
    decoded = base64.b64decode(encoded).decode("utf-8")
    cfg = json.loads(decoded)

    outbound = {
        "type": "vmess",
        "tag": "proxy",
        "server": cfg.get("add", ""),
        "server_port": int(cfg.get("port", 443)),
        "uuid": cfg.get("id", ""),
        "security": cfg.get("scy", "auto"),
        "alter_id": int(cfg.get("aid", 0)),
    }

    # TLS
    if cfg.get("tls") == "tls":
        tls = {"enabled": True}
        sni = cfg.get("sni", "")
        if sni:
            tls["server_name"] = sni
        elif cfg.get("host"):
            tls["server_name"] = cfg["host"]
        alpn = cfg.get("alpn", "")
        if alpn:
            tls["alpn"] = alpn.split(",")
        outbound["tls"] = tls

    # Transport
    net = cfg.get("net", "tcp")
    if net == "ws":
        transport = {"type": "ws"}
        if cfg.get("path"):
            transport["path"] = cfg["path"]
        if cfg.get("host"):
            transport["headers"] = {"Host": cfg["host"]}
        outbound["transport"] = transport
    elif net == "grpc":
        transport = {"type": "grpc"}
        if cfg.get("path"):
            transport["service_name"] = cfg["path"]
        outbound["transport"] = transport
    elif net in ("h2", "http"):
        transport = {"type": "http"}
        if cfg.get("path"):
            transport["path"] = cfg["path"]
        if cfg.get("host"):
            transport["host"] = [cfg["host"]]
        outbound["transport"] = transport

    return outbound


def parse_hysteria2(parsed, params):
    outbound = {
        "type": "hysteria2",
        "tag": "proxy",
        "server": parsed.hostname,
        "server_port": parsed.port or 443,
        "password": unquote(parsed.username or ""),
    }

    tls = {"enabled": True}
    sni = params.get("sni", [""])[0]
    if sni:
        tls["server_name"] = sni
    insecure = params.get("insecure", params.get("allowInsecure", ["0"]))[0]
    if insecure == "1":
        tls["insecure"] = True
    alpn = params.get("alpn", [""])[0]
    if alpn:
        tls["alpn"] = alpn.split(",")
    outbound["tls"] = tls

    # Obfuscation (optional)
    obfs = params.get("obfs", [""])[0]
    if obfs:
        obfs_pwd = params.get("obfs-password", [""])[0]
        outbound["obfs"] = {"type": obfs, "password": obfs_pwd}

    return outbound


def parse_tuic(parsed, params):
    outbound = {
        "type": "tuic",
        "tag": "proxy",
        "server": parsed.hostname,
        "server_port": parsed.port or 443,
        "uuid": "",
        "password": "",
        "congestion_control": params.get("congestion_control", ["bbr"])[0],
    }

    user_part = unquote(parsed.username or "")
    pass_part = unquote(parsed.password or "")

    if ":" in user_part and not pass_part:
        outbound["uuid"], outbound["password"] = user_part.split(":", 1)
    else:
        outbound["uuid"] = user_part
        outbound["password"] = pass_part

    tls = {"enabled": True}
    sni = params.get("sni", [""])[0]
    if sni:
        tls["server_name"] = sni
    insecure = params.get("insecure", params.get("allowInsecure", ["0"]))[0]
    if insecure == "1":
        tls["insecure"] = True
    alpn = params.get("alpn", [""])[0]
    if alpn:
        tls["alpn"] = alpn.split(",")
    outbound["tls"] = tls

    return outbound


# ============================================================
# Main
# ============================================================

def main():
    proxy_url = os.environ.get("PROXY_URL", "").strip()
    if not proxy_url:
        print("PROXY_URL is empty, skipping sing-box config generation.")
        sys.exit(0)

    scheme = proxy_url.split("://")[0].lower()
    print(f"Parsing proxy URI ({scheme}://***)")

    if scheme == "vmess":
        outbound = parse_vmess(proxy_url)
    else:
        parsed = urlparse(proxy_url)
        params = parse_qs(parsed.query)

        if scheme == "socks5":
            outbound = parse_socks5(parsed)
        elif scheme in ("http", "https"):
            outbound = parse_http(parsed)
        elif scheme == "vless":
            outbound = parse_vless(parsed, params)
        elif scheme in ("hy2", "hysteria2"):
            outbound = parse_hysteria2(parsed, params)
        elif scheme == "tuic":
            outbound = parse_tuic(parsed, params)
        else:
            print(f"Unsupported protocol: {scheme}")
            sys.exit(1)

    config = {
        "log": {"level": "debug", "timestamp": True},
        "inbounds": [
            {
                "type": "http",
                "tag": "http-in",
                "listen": LISTEN_HOST,
                "listen_port": LISTEN_PORT,
            }
        ],
        "outbounds": [
            outbound,
            {"type": "direct", "tag": "direct"},
        ],
    }

    with open("config.json", "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    server = outbound.get("server", "N/A")
    port = outbound.get("server_port", "N/A")
    print(f"sing-box config.json generated.")
    print(f"  Inbound: http://{LISTEN_HOST}:{LISTEN_PORT}")
    print(f"  Outbound: {outbound['type']} -> {server}:{port}")
    if outbound.get("type") == "vless":
        tls = outbound.get("tls", {})
        transport = outbound.get("transport", {})
        headers = transport.get("headers", {})
        print(f"  TLS SNI: {tls.get('server_name', '(default)')}")
        print(f"  Transport: {transport.get('type', 'tcp')}")
        if transport.get("type") == "ws":
            print(f"  WS Host: {headers.get('Host', '(default)')}")
            print(f"  WS Path: {transport.get('path', '/')}")
            print(f"  WS Early Data: {transport.get('max_early_data', 0)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error generating sing-box config: {type(e).__name__}")
        sys.exit(1)
