#!/usr/bin/env python3
"""
Step 1: inventory an ensemble's files on HPSS.

    hpss_inventory.py ensembles/cl3_32_64_b6p5_m0p1788.toml
    hpss_inventory.py ensembles/X.toml --listing saved_ls-lR.txt   # offline re-parse

For every stream directory named in the ensemble file this runs

    hsi -q ls -lR <storage.root>/<stream dir>

(or parses a previously saved `hsi ls -lR` dump), and writes

    work/<ensemble>/inventory.json

with one entry per file: path, size, mtime, owner, group, mode. That JSON is
the only thing later steps read, so the slow tape-system listing happens once.

Nothing is staged from tape; `ls` is a metadata-only HPSS operation.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import (  # noqa: E402
    human_bytes, load_catalog_config, load_ensemble, storage_for, utcnow, work_dir, write_json,
)

# `hsi ls -lR` output:
#   /home/w/wdetmold/clover-iso/cl3_32_64_b6p5_m0p1788/cfgs:
#   -rw-r-x---    1 wdetmold  m747      1207975288 Jan 10  2026 cl3_32_64_b6p5_m0p1788_cfg_10.lime
#   drwxr-s---    2 wdetmold  m747           13824 Apr 14  2026 cfgs
#   -rw-r-----    1 wdetmold  m747             512 Oct 14 15:16 recent_file
DIR_HEADER = re.compile(r"^(/\S.*):$")
ENTRY = re.compile(
    r"^(?P<mode>[-dl][rwxsStT-]{9})\s+\d+\s+(?P<owner>\S+)\s+(?P<group>\S+)\s+"
    r"(?P<size>\d+)\s+(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+"
    r"(?P<yt>\d{4}|\d{2}:\d{2})\s+(?P<name>.+?)\s*$"
)


def parse_mtime(mon: str, day: str, yt: str, now: datetime) -> str:
    """Return ISO date (YYYY-MM-DD). `ls` prints HH:MM instead of the year for
    files younger than ~6 months; in that case use the current year unless
    that would put the date in the future."""
    if ":" in yt:
        year = now.year
        d = datetime.strptime(f"{mon} {day} {year}", "%b %d %Y")
        if d > now:
            d = d.replace(year=year - 1)
    else:
        d = datetime.strptime(f"{mon} {day} {yt}", "%b %d %Y")
    return d.strftime("%Y-%m-%d")


def parse_listing(text: str, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    cwd = None
    files = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m = DIR_HEADER.match(line)
        if m:
            cwd = m.group(1)
            continue
        m = ENTRY.match(line)
        if not m:
            continue  # hsi banners, "total" lines, warnings
        if m.group("mode").startswith("d"):
            continue
        if cwd is None:
            continue
        files.append({
            "path": f"{cwd}/{m.group('name')}",
            "size": int(m.group("size")),
            "mtime": parse_mtime(m.group("mon"), m.group("day"), m.group("yt"), now),
            "owner": m.group("owner"),
            "group": m.group("group"),
            "mode": m.group("mode"),
        })
    return files


def run_hsi_ls(hsi: str, path: str) -> str:
    cmd = [hsi, "-q", "ls", "-lR", path]
    sys.stderr.write(f"[hsi] {' '.join(cmd)}\n")
    # hsi writes listings to stderr with -q; capture both.
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = p.stdout + p.stderr
    if p.returncode != 0 and not DIR_HEADER.search(out):
        sys.exit(f"hsi failed (exit {p.returncode}):\n{out[-2000:]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ensemble_toml")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    ap.add_argument("--listing", help="parse this saved `hsi ls -lR` dump instead of running hsi")
    ap.add_argument("-o", "--output", help="output JSON (default: work/<ensemble>/inventory.json)")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)
    ens = load_ensemble(args.ensemble_toml)
    name = ens["ensemble"]["name"]
    storage = storage_for(cfg, ens)
    root = storage["root"].rstrip("/")

    if args.listing:
        text = Path(args.listing).read_text()
        source = f"listing file {args.listing}"
        all_files = parse_listing(text)
    else:
        source = "hsi ls -lR"
        all_files = []
        for label, st in ens["streams"].items():
            all_files += parse_listing(run_hsi_ls(storage.get("hsi", "hsi"), f"{root}/{st['dir']}"))

    streams = {}
    for label, st in ens["streams"].items():
        prefix = f"{root}/{st['dir']}/"
        mine = [dict(f, rel_path=f["path"][len(prefix):]) for f in all_files if f["path"].startswith(prefix)]
        mine.sort(key=lambda f: f["path"])
        if not mine:
            sys.stderr.write(f"[warn] stream {label}: no files found under {prefix}\n")
        streams[label] = {"dir": st["dir"], "hpss_dir": prefix.rstrip("/"), "n_files": len(mine),
                          "total_bytes": sum(f["size"] for f in mine), "files": mine}

    inv = {
        "ensemble": name,
        "storage": storage["key"],
        "root": root,
        "generated_at": utcnow(),
        "source": source,
        "streams": streams,
    }
    out = Path(args.output) if args.output else work_dir(cfg, name) / "inventory.json"
    write_json(out, inv)

    tot_n = sum(s["n_files"] for s in streams.values())
    tot_b = sum(s["total_bytes"] for s in streams.values())
    print(f"{name}: {len(streams)} streams, {tot_n} files, {human_bytes(tot_b)}")
    for label, s in streams.items():
        print(f"  {label:>6}  {s['n_files']:5d} files  {human_bytes(s['total_bytes']):>10}  {s['hpss_dir']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
