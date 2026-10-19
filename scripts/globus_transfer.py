#!/usr/bin/env python3
r"""
Move the files in a manifest between two Globus collections from catalog.toml.

    # 1. plan: write the batch file + plan JSON, print the command, submit nothing
    globus_transfer.py work/cl21_nucleon/manifest.json --src nersc_hpss --dest nersc_dtn \
        --dest-root /pscratch/sd/m/mlwagman/correlators/cl21_nucleon \
        --dest-template "{dir_name}/{basename}"

    # 2. submit (same flags + --submit); add --dirs/--trajectory-min/--limit to do a slice first
    globus_transfer.py ... --submit --label "cl21 nucleon correlators"

    # 3. follow it
    globus_transfer.py --status <task-id> [--wait]

    # 4. when the destination is a local filesystem, check what landed
    globus_transfer.py work/cl21_nucleon/manifest.json --dest-root ... --dest-template ... --verify

Destination path for each file is <dest-root>/<template> where the template may
use {dir_name} (last component of the source directory), {rel_path},
{basename}, and any named group from the selection regex (e.g. {trajectory}).

The batch file is written as `globus transfer --batch` expects (one
"source dest" pair per line) and the transfer is submitted with
--sync-level size, so re-submitting the same batch after a partial failure
only moves what is missing or differs in size. With --verify the script
checks the local destination directly (size match) and writes a JSON report,
which is what untar_manifest.py uses to decide what is safe to unpack.

Consent / session errors from the Globus CLI are printed verbatim; they need
a browser and must be run by a person:
    globus session consent '<scope printed by the error>'
    globus session update nersc.gov
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import dest_path, human_bytes, load_catalog_config, read_json, utcnow, write_json  # noqa: E402


def globus_bin(cfg: dict) -> str:
    """The globus CLI: next to an optional catalog.toml [venv].python, else from PATH."""
    py = cfg.get("venv", {}).get("python")
    if py:
        cand = Path(py).parent / "globus"
        if cand.exists():
            return str(cand)
    return "globus"


def run_globus(cfg: dict, *args: str) -> tuple[int, str]:
    cmd = [globus_bin(cfg), *args]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def select_files(manifest: dict, args) -> list[dict]:
    """Manifest entries that pass the --dirs / --trajectory-min/-max / --limit filters."""
    out = []
    for v in manifest["dirs"].values():
        if args.dirs and v["dir_name"] not in args.dirs:
            continue
        for f in v["files"]:
            t = f.get("trajectory")
            if args.trajectory_min is not None and (t is None or t < args.trajectory_min):
                continue
            if args.trajectory_max is not None and (t is None or t > args.trajectory_max):
                continue
            out.append(f)
    if args.limit:
        out = out[: args.limit]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", nargs="?", help="manifest JSON from hpss_select.py")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    ap.add_argument("--src", help="source storage key in catalog.toml [storage.*]")
    ap.add_argument("--dest", help="destination storage key in catalog.toml [storage.*]")
    ap.add_argument("--dest-root", help="absolute directory on the destination collection")
    ap.add_argument("--dest-template", default="{dir_name}/{rel_path}",
                    help="path under --dest-root; fields: dir_name, rel_path, basename, regex groups")
    ap.add_argument("--dirs", nargs="*", help="only these source dir_names")
    ap.add_argument("--trajectory-min", type=int)
    ap.add_argument("--trajectory-max", type=int)
    ap.add_argument("--limit", type=int, help="first N files only (smoke test)")
    ap.add_argument("--submit", action="store_true", help="actually submit the transfer")
    ap.add_argument("--label", default="usqcd-catalog transfer")
    ap.add_argument("--sync-level", default="size", choices=["exists", "size", "mtime", "checksum"])
    ap.add_argument("--verify", action="store_true", help="check local destination files (size) instead of transferring")
    ap.add_argument("--status", metavar="TASK_ID", help="show a submitted task")
    ap.add_argument("--wait", action="store_true", help="with --status: block until the task finishes")
    ap.add_argument("--work", help="directory for batch/plan/report files (default: next to the manifest)")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)

    # ---- status / wait
    if args.status:
        if args.wait:
            rc, out = run_globus(cfg, "task", "wait", args.status, "--timeout", "86400", "--polling-interval", "60")
            print(out.strip())
        rc, out = run_globus(cfg, "task", "show", args.status, "--format", "json")
        if rc != 0:
            print(out, file=sys.stderr)
            return rc
        t = json.loads(out)
        print(json.dumps({k: t.get(k) for k in ("task_id", "status", "nice_status", "label", "files", "files_skipped",
                                                 "files_transferred", "bytes_transferred", "effective_bytes_per_second",
                                                 "faults", "request_time", "completion_time")}, indent=1))
        return 0 if t.get("status") in ("SUCCEEDED", "ACTIVE") else 1

    if not (args.manifest and args.dest_root):
        sys.exit("need a manifest and --dest-root (or --status TASK_ID)")
    manifest = read_json(args.manifest)
    files = select_files(manifest, args)
    work = Path(args.work) if args.work else Path(args.manifest).resolve().parent
    work.mkdir(parents=True, exist_ok=True)
    pairs = [(f["path"], dest_path(f, args.dest_root, args.dest_template), f["size"]) for f in files]
    total = sum(size for _, _, size in pairs)

    # ---- verify local destination
    if args.verify:
        ok, missing, bad = [], [], []
        bytes_ok = 0
        for src, dst, size in pairs:
            if not os.path.exists(dst):
                missing.append(dst)
            elif os.path.getsize(dst) != size:
                bad.append({"path": dst, "expected": size, "actual": os.path.getsize(dst)})
            else:
                ok.append(dst)
                bytes_ok += size
        rep = {"checked_at": utcnow(), "manifest": str(Path(args.manifest).resolve()), "dest_root": args.dest_root,
               "n_ok": len(ok), "n_missing": len(missing), "n_size_mismatch": len(bad),
               "bytes_ok": bytes_ok, "missing": missing, "size_mismatch": bad, "ok": ok}
        write_json(work / "verify.json", rep)
        print(f"{len(ok)} ok ({human_bytes(rep['bytes_ok'])}), {len(missing)} missing, {len(bad)} size mismatch; "
              f"wrote {work / 'verify.json'}")
        return 0 if not (missing or bad) else 1

    # ---- plan / submit
    if not (args.src and args.dest):
        sys.exit("--src and --dest storage keys are required to plan a transfer")
    try:
        src_uuid = cfg["storage"][args.src]["globus_collection"]
        dst_uuid = cfg["storage"][args.dest]["globus_collection"]
    except KeyError as e:
        sys.exit(f"storage key {e} not in catalog.toml or has no globus_collection")

    stamp = time.strftime("%Y%m%dT%H%M%S")
    batch = work / f"globus_batch_{stamp}.txt"
    with open(batch, "w") as fh:
        for src, dst, _ in pairs:
            fh.write(f"{shlex.quote(src)} {shlex.quote(dst)}\n")
    cmd = [globus_bin(cfg), "transfer", src_uuid, dst_uuid, "--batch", str(batch), "--label", args.label,
           "--sync-level", args.sync_level, "--notify", "off", "--format", "json"]
    plan = {"created_at": utcnow(), "manifest": str(Path(args.manifest).resolve()), "src": args.src, "src_uuid": src_uuid,
            "dest": args.dest, "dest_uuid": dst_uuid, "dest_root": args.dest_root, "dest_template": args.dest_template,
            "n_files": len(pairs), "total_bytes": total, "batch_file": str(batch), "command": " ".join(shlex.quote(c) for c in cmd),
            "files": [{"src": s, "dest": d, "size": z} for s, d, z in pairs]}
    write_json(work / f"transfer_plan_{stamp}.json", plan)
    print(f"{len(pairs)} files, {human_bytes(total)}: {cfg['storage'][args.src]['globus_label']} -> "
          f"{cfg['storage'][args.dest]['globus_label']}:{args.dest_root}")
    print(f"batch file: {batch}")
    if not args.submit:
        print("not submitted (add --submit). Command that would run:")
        print("  " + plan["command"])
        return 0

    rc, out = run_globus(cfg, *cmd[1:])
    if rc != 0:
        print(out.strip(), file=sys.stderr)
        if "consent" in out or "session" in out.lower():
            print("\nA person must run the command above in a browser session, then re-run with --submit.", file=sys.stderr)
        return rc
    task = json.loads(out)
    task_id = task.get("task_id")
    rec = {"submitted_at": utcnow(), "task_id": task_id, "label": args.label, "n_files": len(pairs), "total_bytes": total,
           "batch_file": str(batch), "plan": str(work / f"transfer_plan_{stamp}.json")}
    log = work / "transfers.json"
    hist = read_json(log) if log.exists() else []
    hist.append(rec)
    write_json(log, hist)
    print(f"submitted task {task_id}: {len(pairs)} files / {human_bytes(total)} queued (not yet transferred)")
    print(f"follow with: {sys.argv[0]} --status {task_id} [--wait]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
