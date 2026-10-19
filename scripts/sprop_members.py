#!/usr/bin/env python3
r"""
Summarise the members of sparse-propagator htar archives on HPSS.

    sprop_members.py --inventory work/<ens>/inventory.json --match 'sparseprops.*/\d+\.tar$' \
                     -o work/<ens>_sparseprops/members.json [--jobs 4] [--resume]
    sprop_members.py --archives /home/w/.../sparseprops-t0/1000.tar ... -o members.json

Runs `htar -tvf <archive>` (reads only the small .idx file, no tape access)
and records, per archive, what the member names say:

  archives[<archive path>] = {
      "n_members": 1024, "total_bytes": ..., "trajectory_min": 1000, "trajectory_max": 1000,
      "prop": {
          "tsrc_list": [0],                       # source timeslices present
          "flavors": ["light", "strange"],         # u -> light, s -> strange
          "smearing_combinations": ["S3.0_S3.0"], # <source>_<sink> widths
          "n_positions": 512,                      # distinct (x, y, z) source sites
          "member_bytes": 113312192                # size of one .prop member
      }
  }
  no_index[<archive path>] = "<htar error>"      # archives without a readable .idx

build_records.py --archive-index members.json puts the "prop" block on each
archive's record as lqcd.prop.* and n_members as lqcd.prop.n_members, so
the catalog says what each archive holds even where directory names are
only nominal (e.g. 2025 random-source-timeslice sets stored under
sparseprops-t0/). Output is written incrementally; --resume skips archives
already summarised.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import human_bytes, read_json, utcnow, write_json  # noqa: E402

# HTAR: -rw-rw----  wdetmold/wdetmold  113312192 2025-04-01 17:25  1000/sprop_x0y0z0t0_sS3.0_S3.0.prop
HTAR_LINE = re.compile(r"^HTAR:\s+(?P<mode>\S+)\s+\S+\s+(?P<size>\d+)\s+\S+\s+\S+\s+(?P<path>.+?)\s*$")
# NPLQCD sprop member names, including the smearing-test variants that carry
# step counts (S7.0N100) and a second position block (_fx..fy..fz..ft..).
SPROP = re.compile(
    r"sprop_x(?P<x>\d+)y(?P<y>\d+)z(?P<z>\d+)t(?P<t>\d+)(?:_fx\d+fy\d+fz\d+ft\d+)?"
    r"_(?P<f>[a-z])S(?P<wsrc>\d+(?:\.\d+)?)(?:N\d+)?_S(?P<wsnk>\d+(?:\.\d+)?)(?:N\d+)?")
FLAVOR = {"u": "light", "d": "light", "s": "strange", "c": "charm"}


def htar_list(archive: str) -> tuple[str, str | None]:
    p = subprocess.run(["htar", "-tvf", archive], capture_output=True, text=True)
    out = p.stdout + p.stderr
    if p.returncode != 0 or "HTAR: HTAR SUCCESSFUL" not in out:
        err = [l for l in out.splitlines() if "ERROR" in l or "error" in l.lower()]
        return out, (err[-1] if err else out[-300:]).strip()
    return out, None


def summarise(text: str) -> dict:
    n = total = 0
    trajs, tsrc, flavors, combos, positions, sizes = set(), set(), set(), set(), set(), set()
    for line in text.splitlines():
        m = HTAR_LINE.match(line)
        if not m or m.group("mode").startswith("d") or m.group("path").startswith("/tmp/HTAR_CF_CHK"):
            continue
        n += 1
        total += int(m.group("size"))
        path = m.group("path")
        head = path.split("/", 1)[0]
        if head.isdigit():
            trajs.add(int(head))
        sm = SPROP.search(path)
        if sm:
            tsrc.add(int(sm.group("t")))
            flavors.add(FLAVOR.get(sm.group("f"), sm.group("f")))
            combos.add(f"S{sm.group('wsrc')}_S{sm.group('wsnk')}")
            positions.add((sm.group("x"), sm.group("y"), sm.group("z")))
            sizes.add(int(m.group("size")))
    out = {"n_members": n, "total_bytes": total,
           "trajectory_min": min(trajs) if trajs else None, "trajectory_max": max(trajs) if trajs else None}
    if tsrc:
        out["prop"] = {"tsrc_list": sorted(tsrc), "flavors": sorted(flavors),
                       "smearing_combinations": sorted(combos), "n_positions": len(positions)}
        if len(sizes) == 1:
            out["prop"]["member_bytes"] = sizes.pop()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--archives", nargs="+", help="absolute HPSS paths of htar archives")
    src.add_argument("--inventory", help="hpss_inventory.py JSON; archives are its files matching --match")
    ap.add_argument("--match", default=r"\.tar$", help="regex on the HPSS path (with --inventory)")
    ap.add_argument("--jobs", type=int, default=4, help="parallel htar listings (default 4)")
    ap.add_argument("--resume", action="store_true", help="skip archives already in the output file")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    if args.inventory:
        rx = re.compile(args.match)
        archives = sorted(f["path"] for s in read_json(args.inventory)["streams"].values()
                          for f in s["files"] if rx.search(f["path"]))
    else:
        archives = list(args.archives)
    out_path = Path(args.output)
    result = {"generated_at": utcnow(), "archives": {}, "no_index": {}}
    if args.resume and out_path.exists():
        prev = read_json(out_path)
        result["archives"] = prev.get("archives", {})
        result["no_index"] = prev.get("no_index", {})
        archives = [a for a in archives if a not in result["archives"]]
    sys.stderr.write(f"[sprop_members] {len(archives)} archives to list with {args.jobs} workers\n")

    def work(arc: str):
        text, err = htar_list(arc)
        return arc, (None if err else summarise(text)), err

    done = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for fut in as_completed([pool.submit(work, a) for a in archives]):
            arc, summary, err = fut.result()
            if err:
                result["no_index"][arc] = err
            else:
                result["archives"][arc] = summary
                result["no_index"].pop(arc, None)
            done += 1
            if done % 50 == 0 or done == len(archives):
                write_json(out_path, result)
                sys.stderr.write(f"  {done}/{len(archives)} listed, {len(result['no_index'])} without index\n")
    write_json(out_path, result)
    n_members = sum(v["n_members"] for v in result["archives"].values())
    total = sum(v["total_bytes"] for v in result["archives"].values())
    print(f"{len(result['archives'])} archives summarised ({n_members} members, {human_bytes(total)}), "
          f"{len(result['no_index'])} without a readable index -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
