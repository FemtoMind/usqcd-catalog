#!/usr/bin/env python3
"""
Merge several sprop_members.py outputs into one: merge_members.py -o merged.json part1.json part2.json ...

Useful when one large set of archives was indexed by several xfer-queue jobs
with disjoint --match patterns. An archive summarised in any input wins over
a "no_index" entry for the same path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import read_json, utcnow, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parts", nargs="+")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    merged = {"generated_at": utcnow(), "archives": {}, "no_index": {}}
    for p in args.parts:
        part = read_json(p)
        merged["archives"].update(part.get("archives", {}))
        for arc, err in part.get("no_index", {}).items():
            merged["no_index"].setdefault(arc, err)
    for arc in merged["archives"]:
        merged["no_index"].pop(arc, None)
    write_json(Path(args.output), merged)
    print(f"{len(merged['archives'])} archives, {len(merged['no_index'])} without index -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
