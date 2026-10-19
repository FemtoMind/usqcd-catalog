#!/usr/bin/env python3
r"""
Index the members of htar archives on HPSS, grouped by trajectory and source.

    htar_members.py --archives /home/w/.../cl21_..._-djm-2/frontier/correlators-NN/processed-results.tar ... \
                    --pattern '^processed-results/all-corrs-(?P<trajectory>\d+)-(?P<tsrc>\d+)\.h5$' \
                    -o work/cl21_nucleon/members.json
    htar_members.py --listing dir_with_saved_htar_tvf_outputs/ ...      # offline re-parse

Runs `htar -tvf <archive>` (reads only the small .idx file, no tape access),
keeps members matching --pattern, and writes a manifest:

  archives[<archive path>] = {
      "stream": <last path component of the stream dir>,
      "n_members", "total_bytes",
      "trajectories": {<traj>: {"members": [{"path","size","tsrc"}], "n_tsrc"}},
      "tsrc_counts": {<n_tsrc>: <how many trajectories have that many sources>}
  }

This is the input to reduce_corrs.py, which pulls one trajectory's members at
a time with bin/htar_extract.sh.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import human_bytes, utcnow, write_json  # noqa: E402

# HTAR: -rw-r--r--  agrebe/agrebe  363722240 2026-02-26 18:53  processed-results/all-corrs-1000-0.h5
HTAR_LINE = re.compile(r"^HTAR:\s+(?P<mode>\S+)\s+\S+\s+(?P<size>\d+)\s+\S+\s+\S+\s+(?P<path>.+?)\s*$")


def htar_list(archive: str) -> str:
    p = subprocess.run(["htar", "-tvf", archive], capture_output=True, text=True)
    out = p.stdout + p.stderr
    if p.returncode != 0 and "HTAR:" not in out:
        sys.exit(f"htar -tvf {archive} failed:\n{out[-1500:]}")
    return out


def stream_of(archive: str, depth: int) -> str:
    """Stream directory name = the path component `depth` levels above the archive file."""
    parts = archive.rstrip("/").split("/")
    return parts[-1 - depth]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archives", nargs="+", required=True, help="absolute HPSS paths of htar archives")
    ap.add_argument("--pattern", required=True, help="regex on member path; groups trajectory and tsrc are used")
    ap.add_argument("--listing", help="directory holding saved `htar -tvf` outputs named <stream>.lst")
    ap.add_argument("--stream-depth", type=int, default=3,
                    help="how many path levels above the archive the stream dir is (default 3: <stream>/frontier/correlators-NN/x.tar)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    pat = re.compile(args.pattern)
    archives = {}
    for arc in args.archives:
        stream = stream_of(arc, args.stream_depth)
        if args.listing:
            text = (Path(args.listing) / f"{stream}.lst").read_text()
        else:
            sys.stderr.write(f"[htar] -tvf {arc}\n")
            text = htar_list(arc)
        trajs: dict[int, dict] = {}
        n = 0
        total = 0
        for line in text.splitlines():
            m = HTAR_LINE.match(line)
            if not m or m.group("mode").startswith("d"):
                continue
            pm = pat.match(m.group("path"))
            if not pm:
                continue
            g = pm.groupdict()
            t = int(g["trajectory"])
            rec = {"path": m.group("path"), "size": int(m.group("size"))}
            if "tsrc" in g and g["tsrc"] is not None:
                rec["tsrc"] = int(g["tsrc"])
            trajs.setdefault(t, {"members": []})["members"].append(rec)
            n += 1
            total += rec["size"]
        for t, v in trajs.items():
            v["members"].sort(key=lambda r: r.get("tsrc", 0))
            v["n_tsrc"] = len(v["members"])
            v["bytes"] = sum(r["size"] for r in v["members"])
        archives[arc] = {
            "stream": stream, "n_members": n, "total_bytes": total,
            "n_trajectories": len(trajs),
            "trajectory_min": min(trajs) if trajs else None, "trajectory_max": max(trajs) if trajs else None,
            "tsrc_counts": dict(Counter(v["n_tsrc"] for v in trajs.values())),
            "trajectories": {str(t): trajs[t] for t in sorted(trajs)},
        }

    out = {"generated_at": utcnow(), "pattern": args.pattern, "archives": archives}
    write_json(Path(args.output), out)
    print(f"{'stream':44} {'trajs':>5} {'members':>7} {'size':>8}  traj range   sources/traj")
    for arc, v in archives.items():
        print(f"{v['stream']:44} {v['n_trajectories']:5d} {v['n_members']:7d} {human_bytes(v['total_bytes']):>8}  "
              f"{v['trajectory_min']}..{v['trajectory_max']}   {v['tsrc_counts']}")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
