#!/usr/bin/env python3
r"""
Select files on HPSS by directory + regex and write a transfer manifest.

    hpss_select.py --dirs /home/w/wdetmold/clover-iso/cl21_48_96_b6p3_m0p2416_m0p2050-djm-1 ... \
                   --pattern '^frontier/correlators/(?P<trajectory>\d+)\.tar$' \
                   -o work/cl21_nucleon/manifest.json
    hpss_select.py --listing saved_ls-lR.txt --dirs ... --pattern ... -o manifest.json   # offline

Runs `hsi ls -lR` on each directory (or parses a saved dump), keeps the files
whose path *relative to that directory* matches --pattern, and writes a
manifest: per directory the matching files (path, size, mtime, rel_path, any
named regex groups such as "trajectory") plus counts and totals. It prints a
one-line-per-directory summary so you can see at a glance which streams have
what, and which trajectories are missing between min and max (if --step).

The manifest is the input to globus_transfer.py and untar_manifest.py, so a
data-movement job is: select -> transfer -> unpack, each re-runnable.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import human_bytes, utcnow, write_json  # noqa: E402
from hpss_inventory import parse_listing, run_hsi_ls  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dirs", nargs="+", required=True, help="absolute HPSS directories to search")
    ap.add_argument("--pattern", required=True, help="regex on the path relative to each directory")
    ap.add_argument("--listing", help="saved `hsi ls -lR` output covering the dirs (skip the live hsi)")
    ap.add_argument("--hsi", default="hsi")
    ap.add_argument("--step", type=int, help="expected trajectory spacing; report gaps between min and max")
    ap.add_argument("-o", "--output", required=True, help="manifest JSON to write")
    args = ap.parse_args()

    pat = re.compile(args.pattern)
    if args.listing:
        all_files = parse_listing(Path(args.listing).read_text())
    else:
        all_files = []
        for d in args.dirs:
            all_files += parse_listing(run_hsi_ls(args.hsi, d))

    dirs = {}
    for d in args.dirs:
        d = d.rstrip("/")
        prefix = d + "/"
        dir_name = d.rsplit("/", 1)[1]
        hits = []
        for f in all_files:
            if not f["path"].startswith(prefix):
                continue
            rel = f["path"][len(prefix):]
            m = pat.match(rel)
            if not m:
                continue
            rec = dict(f, rel_path=rel, dir=d, dir_name=dir_name)
            for k, v in m.groupdict().items():
                if v is not None:
                    rec[k] = int(v) if v.isdigit() else v
            hits.append(rec)
        hits.sort(key=lambda r: (r.get("trajectory", 0), r["path"]))
        trajs = [h["trajectory"] for h in hits if isinstance(h.get("trajectory"), int)]
        gaps = []
        if args.step and trajs:
            have = set(trajs)
            gaps = [t for t in range(min(trajs), max(trajs) + 1, args.step) if t not in have]
        dirs[d] = {
            "dir_name": dir_name, "n_files": len(hits), "total_bytes": sum(h["size"] for h in hits),
            "trajectory_min": min(trajs) if trajs else None, "trajectory_max": max(trajs) if trajs else None,
            "trajectories": trajs, "gaps": gaps, "files": hits,
        }

    manifest = {"generated_at": utcnow(), "pattern": args.pattern, "source": args.listing or "hsi ls -lR",
                "n_files": sum(v["n_files"] for v in dirs.values()),
                "total_bytes": sum(v["total_bytes"] for v in dirs.values()), "dirs": dirs}
    write_json(Path(args.output), manifest)

    print(f"{'directory':58} {'files':>5} {'size':>10}  trajectories")
    for d, v in dirs.items():
        rng = f"{v['trajectory_min']}..{v['trajectory_max']}" if v["trajectory_min"] is not None else "-"
        gap = f"  ({len(v['gaps'])} gaps)" if v["gaps"] else ""
        print(f"{v['dir_name']:58} {v['n_files']:5d} {human_bytes(v['total_bytes']):>10}  {rng}{gap}")
    print(f"{'total':58} {manifest['n_files']:5d} {human_bytes(manifest['total_bytes']):>10}")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
