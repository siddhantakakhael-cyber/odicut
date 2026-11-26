"""Download the OpenCode platform binary from the npm registry into vendor/.

Used by CI to bundle OpenCode inside the exe. Standalone (no ochub import) so it
runs before dependencies are fully wired.
"""
from __future__ import annotations
import argparse
import io
import sys
import tarfile
from pathlib import Path

import requests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", default="opencode-windows-x64")
    ap.add_argument("--dest", default="vendor/opencode.exe")
    args = ap.parse_args()

    meta = requests.get(f"https://registry.npmjs.org/{args.pkg}", timeout=60).json()
    latest = meta.get("dist-tags", {}).get("latest")
    if not latest:
        print(f"ERROR: no latest version for {args.pkg}", file=sys.stderr)
        return 1
    tarball = meta["versions"][latest]["dist"]["tarball"]
    print(f"Downloading {args.pkg}@{latest} …")
    data = requests.get(tarball, timeout=600).content
    dest = Path(args.dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        member = next((m for m in tf.getmembers()
                       if m.name.endswith(("bin/opencode.exe", "bin/opencode"))), None)
        if member is None:
            print("ERROR: binary not found in tarball", file=sys.stderr)
            return 1
        dest.write_bytes(tf.extractfile(member).read())
    print(f"Saved {dest} ({dest.stat().st_size:,} bytes), OpenCode {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
