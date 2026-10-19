#!/usr/bin/env python3
"""
Survey a storage tree before writing [[files]] rules: per directory, how many
files, how big, and what the names look like with digits collapsed to N.

    survey_listing.py --listing saved_hsi_ls-lR.txt [--match sparseprops] [--top 6]
    survey_listing.py --hsi /home/w/wdetmold/clover-iso/<dir> ...   # runs hsi ls -lR
    survey_listing.py --inventory work/<ensemble>/inventory.json

Typical use: find out which directories hold which artifact kinds and what
regex will match them, e.g.

    cl3_48_96_b6p5_m0p1788/sparseprops-t24: 1534 files, 112.3 TB
        767    112303.1 GB  N.tar
        767         0.8 GB  N.tar.idx

--find REGEX instead prints every full path matching REGEX (with size and
date), for pinning down a specific file.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import human_bytes, read_json  # noqa: E402
from hpss_inventory import parse_listing, run_hsi_ls  # noqa: E402


def load_files(args) -> list[dict]:
    if args.listing:
        return parse_listing(Path(args.listing).read_text())
    if args.inventory:
        inv = read_json(args.inventory)
        return [f for s in inv["streams"].values() for f in s["files"]]
    files = []
    for d in args.hsi:
        files += parse_listing(run_hsi_ls("hsi", d))
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--listing", help="saved `hsi ls -lR` output")
    src.add_argument("--inventory", help="inventory.json from hpss_inventory.py")
    src.add_argument("--hsi", nargs="+", metavar="DIR", help="directories to list live with hsi")
    ap.add_argument("--match", help="only directories whose path contains this substring")
    ap.add_argument("--find", metavar="REGEX", help="print full paths matching REGEX instead of the summary")
    ap.add_argument("--top", type=int, default=6, help="name patterns to show per directory")
    ap.add_argument("--strip", default="", help="prefix to strip from printed directory names")
    args = ap.parse_args()

    files = load_files(args)
    if args.find:
        pat = re.compile(args.find)
        for f in files:
            if pat.search(f["path"]):
                print(f"{f['size']:>14} {f.get('mtime', '')} {f['path']}")
        return 0

    by_dir: dict[str, list] = defaultdict(list)
    for f in files:
        d, n = f["path"].rsplit("/", 1)
        if args.match and args.match not in d:
            continue
        by_dir[d].append((n, f["size"]))
    for d in sorted(by_dir):
        entries = by_dir[d]
        pats: dict[str, list] = defaultdict(lambda: [0, 0])
        for n, s in entries:
            p = pats[re.sub(r"\d+", "N", n)]
            p[0] += 1
            p[1] += s
        print(f"{d[len(args.strip):] if d.startswith(args.strip) else d}: {len(entries)} files, "
              f"{human_bytes(sum(s for _, s in entries))}")
        for p, (c, b) in sorted(pats.items(), key=lambda kv: -kv[1][0])[: args.top]:
            print(f"    {c:6d}  {human_bytes(b):>10}  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
