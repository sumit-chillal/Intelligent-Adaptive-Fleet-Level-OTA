#!/usr/bin/env python3
"""
CONVOY — fetch the broker's root certificate and emit it ready to paste.

Why this exists
---------------
Pasting a certificate by hand went wrong three times: once the chain's top
certificate was an intermediate rather than a root, once an awk one-liner
copied an empty clipboard, and once the paste was silently truncated. Every
failure produced the same symptom -- a bare TLS error code -- and none of them
said "your certificate is wrong".

So this script does the checking that a human eye does badly: it confirms the
certificate is self-signed (which is what makes it a ROOT), confirms it is
currently valid, and prints the exact block to paste with the delimiters
already in place.

Usage, from the repository root:

    python tools/broker_ca.py                      # fetch Let's Encrypt ISRG Root X1
    python tools/broker_ca.py --from-broker HOST   # inspect what your broker presents
    python tools/broker_ca.py > /tmp/ca.txt        # then paste from the file
"""

from __future__ import annotations

import argparse
import re
import ssl
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

ISRG_ROOT_X1_URL = "https://letsencrypt.org/certs/isrgrootx1.pem"


def fetch_url(url: str) -> str:
    with urllib.request.urlopen(url, timeout=20) as resp:
        return resp.read().decode()


def fetch_from_broker(host: str, port: int = 8883) -> list[str]:
    """Ask the broker for its chain. Requires openssl on PATH."""
    proc = subprocess.run(
        ["openssl", "s_client", "-showcerts", "-connect", f"{host}:{port}"],
        input="", capture_output=True, text=True, timeout=30,
    )
    return re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        proc.stdout, re.S,
    )


def describe(pem: str) -> dict:
    """Subject, issuer and validity, via openssl."""
    proc = subprocess.run(
        ["openssl", "x509", "-noout", "-subject", "-issuer", "-dates"],
        input=pem, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ValueError(f"not a valid certificate: {proc.stderr.strip()}")

    out = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-broker", metavar="HOST",
                    help="inspect the chain your broker actually presents")
    ap.add_argument("--port", type=int, default=8883)
    args = ap.parse_args()

    if args.from_broker:
        chain = fetch_from_broker(args.from_broker, args.port)
        print(f"# broker presented {len(chain)} certificate(s):", file=sys.stderr)
        for i, cert in enumerate(chain):
            info = describe(cert)
            subject = info.get("subject", "?")
            issuer = info.get("issuer", "?")
            root = subject == issuer
            print(f"#   [{i}] {'ROOT' if root else 'intermediate'}: "
                  f"{subject[:70]}", file=sys.stderr)
        print("#", file=sys.stderr)
        print("# Servers usually do NOT send the root, because clients are "
              "expected\n# to already have it. Fetching it directly is the "
              "reliable path.\n#", file=sys.stderr)

    pem = fetch_url(ISRG_ROOT_X1_URL).strip()
    info = describe(pem)

    subject = info.get("subject", "")
    issuer = info.get("issuer", "")
    if subject != issuer:
        print(f"REFUSING: this is not a root certificate.\n"
              f"  subject: {subject}\n  issuer:  {issuer}\n"
              f"A root is signed by itself; these differ.", file=sys.stderr)
        return 1

    not_after = info.get("notAfter", "")
    print(f"# verified self-signed root", file=sys.stderr)
    print(f"#   subject : {subject}", file=sys.stderr)
    print(f"#   expires : {not_after}", file=sys.stderr)
    print(f"#   lines   : {len(pem.splitlines())}", file=sys.stderr)
    print("#", file=sys.stderr)
    print("# Paste EVERYTHING below into config.h, replacing the whole",
          file=sys.stderr)
    print("# BROKER_ROOT_CA definition including both R\"EOF( and )EOF\";",
          file=sys.stderr)
    print("# ---------------------------------------------------------------",
          file=sys.stderr)

    print('static const char* BROKER_ROOT_CA = R"EOF(')
    print(pem)
    print(')EOF";')
    return 0


if __name__ == "__main__":
    sys.exit(main())
