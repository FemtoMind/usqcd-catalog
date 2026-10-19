#!/usr/bin/env python3
"""
Unpack the tarballs named in a manifest once they have landed on local disk.

    untar_manifest.py work/cl21_nucleon/manifest.json \
        --dest-root /pscratch/sd/m/mlwagman/correlators/cl21_nucleon \
        --dest-template "{dir_name}/{basename}" --jobs 8 [--dry-run] [--remove-tar]

For every manifest file the local tarball is <dest-root>/<template>. Each one is
  * skipped if a marker <tar>.extracted exists (already done),
  * reported as "missing" / "incomplete" if absent or not the manifest size
    (transfer not finished: run globus_transfer.py --verify / --status),
  * otherwise extracted with `tar -xf` into the tarball's own directory, and
    the marker is written with the extraction time and the member count.
--remove-tar deletes a tarball after a successful extraction (the marker and
the manifest still record what it contained). Safe to re-run; it only does
work that is not done yet. Run it under Slurm for anything large
(bin/untar_slurm.sh), not on a login node.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import dest_path, read_json, utcnow, write_json  # noqa: E402


def extract(tar: str, remove: bool, dry: bool) -> tuple[str, str, str]:
    """Extract one tarball next to itself; returns (tar, status, info) for the report."""
    marker = tar + ".extracted"
    outdir = os.path.dirname(tar)
    if dry:
        return tar, "would-extract", outdir
    # count members first so the marker records what was unpacked
    lst = subprocess.run(["tar", "-tf", tar], capture_output=True, text=True)
    if lst.returncode != 0:
        return tar, "error", f"tar -tf failed: {lst.stderr.strip()[:300]}"
    members = lst.stdout.splitlines()
    x = subprocess.run(["tar", "-xf", tar, "-C", outdir], capture_output=True, text=True)
    if x.returncode != 0:
        return tar, "error", f"tar -xf failed: {x.stderr.strip()[:300]}"
    with open(marker, "w") as fh:
        fh.write(f"extracted_at={utcnow()}\nmembers={len(members)}\nsize={os.path.getsize(tar)}\n")
        for m in members[:5]:
            fh.write(f"first_members={m}\n")
    if remove:
        os.remove(tar)
    return tar, "extracted", f"{len(members)} members" + (", tar removed" if remove else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest")
    ap.add_argument("--dest-root", required=True)
    ap.add_argument("--dest-template", default="{dir_name}/{rel_path}")
    ap.add_argument("--dirs", nargs="*", help="only these dir_names")
    ap.add_argument("--jobs", type=int, default=4, help="parallel tar processes")
    ap.add_argument("--remove-tar", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest = read_json(args.manifest)
    todo, done, missing, incomplete = [], [], [], []
    for v in manifest["dirs"].values():
        if args.dirs and v["dir_name"] not in args.dirs:
            continue
        for f in v["files"]:
            tar = dest_path(f, args.dest_root, args.dest_template)
            if os.path.exists(tar + ".extracted"):
                done.append(tar)
            elif not os.path.exists(tar):
                missing.append(tar)
            elif os.path.getsize(tar) != f["size"]:
                incomplete.append(tar)
            else:
                todo.append(tar)
    print(f"{len(done)} already extracted, {len(todo)} to extract, {len(missing)} not transferred yet, "
          f"{len(incomplete)} incomplete on disk")

    results = []
    if todo:
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            for tar, status, info in ex.map(lambda t: extract(t, args.remove_tar, args.dry_run), todo):
                results.append({"tar": tar, "status": status, "info": info})
                print(f"  {status:14} {tar}  {info}")
    n_err = sum(r["status"] == "error" for r in results)
    rep = {"run_at": utcnow(), "manifest": str(Path(args.manifest).resolve()), "dest_root": args.dest_root,
           "dry_run": args.dry_run, "already_done": len(done), "extracted": sum(r["status"] == "extracted" for r in results),
           "errors": n_err, "missing": missing, "incomplete": incomplete, "results": results}
    out = Path(args.manifest).resolve().parent / "untar_report.json"
    write_json(out, rep)
    print(f"wrote {out}")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
