#!/usr/bin/env python3
"""Public-safe PF489 reachability-only check. No authentication or reservation actions."""
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

HOST = "www4.pf489.com"
URLS = (
    "https://www4.pf489.com/kunitachi/web/Wg_ModeSelect.aspx",
    "https://www4.pf489.com/Kokubunji/WebR/",
)


def record(phase: str, **fields):
    print("PF489_DIAG " + json.dumps({"phase": phase, **fields}, ensure_ascii=False), flush=True)


def run():
    record("START", platform=sys.platform)
    try:
        info = socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP, type=socket.SOCK_STREAM)
        addresses = sorted({item[4][0] for item in info})
        record("DNS_OK", host=HOST, addresses=addresses)
    except Exception as exc:
        record("DNS_FAIL", error=f"{type(exc).__name__}: {exc}")
        return 2

    tcp = False
    tls = False
    for af, socktype, proto, _, sockaddr in info:
        start = time.monotonic()
        try:
            with socket.socket(af, socktype, proto) as conn:
                conn.settimeout(5)
                conn.connect(sockaddr)
                tcp = True
                record("TCP_OK", address=sockaddr[0], elapsed_ms=round((time.monotonic()-start)*1000))
                with ssl.create_default_context().wrap_socket(conn, server_hostname=HOST) as secure:
                    tls = True
                    record("TLS_OK", protocol=secure.version())
            break
        except Exception as exc:
            record("TCP_OR_TLS_FAIL", address=sockaddr[0], error=f"{type(exc).__name__}: {exc}")
    if not tcp or not tls:
        record("CONCLUSION", reachable=False, reason="TCP_OR_TLS_UNAVAILABLE")
        return 2

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    good = 0
    for url in URLS:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PF489 connectivity check)"})
        started = time.monotonic()
        try:
            with opener.open(request, timeout=8) as response:
                record("HTTP_OK", url=url, status=response.status, final_url=response.geturl(), elapsed_ms=round((time.monotonic()-started)*1000))
                good += 1
        except urllib.error.HTTPError as exc:
            record("HTTP_STATUS", url=url, status=exc.code, elapsed_ms=round((time.monotonic()-started)*1000))
        except Exception as exc:
            record("HTTP_FAIL", url=url, error=f"{type(exc).__name__}: {exc}")
    record("CONCLUSION", reachable=(good == len(URLS)), http_pass_count=good, http_total=len(URLS))
    return 0 if good == len(URLS) else 1


if __name__ == "__main__":
    raise SystemExit(run())
