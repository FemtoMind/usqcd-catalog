#!/usr/bin/env python3
"""
Step 2: turn an ensemble description + HPSS inventory into MetaCat records.

    build_records.py ensembles/cl3_32_64_b6p5_m0p1788.toml

Reads  work/<ensemble>/inventory.json   (from hpss_inventory.py)
Writes work/<ensemble>/records.json     (consumed by declare_records.py)

records.json is the complete, reviewable statement of what will be put in the
catalog: one dataset record for the ensemble and one file record per inventory
entry that matches a [[files]] rule. Nothing is sent anywhere by this step, so
it is safe to run, inspect, edit the TOML, and run again.

Record layout (what MetaCat's declare_files expects, plus our conventions):

  dataset.did          "lqcd:<ensemble>"
  dataset.metadata     AmSC.common.* + lqcd.* (ensemble-level, flattened)
  files[i].name        file basename (must be unique within the namespace;
                       stream suffixes in NPLQCD file names guarantee this,
                       and the script aborts if two inventory entries collide)
  files[i].size        bytes, from the HPSS listing
  files[i].metadata    AmSC.common.* + lqcd.* (ensemble-level copied in,
                       plus lqcd.artifact.kind, lqcd.stream, lqcd.trajectory,
                       lqcd.storage.*)

Checksums are deliberately absent: HPSS stores none for these files and the
bytes are only on tape. A later pass can add them with update_file().
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import (  # noqa: E402
    classify, complete_mass_kappa, dataset_did, flatten, human_bytes, load_catalog_config,
    load_ensemble, read_json, storage_for, utcnow, work_dir, write_json,
)


def ensemble_metadata(ens: dict, cfg: dict, streams_summary: dict) -> dict:
    e = ens["ensemble"]
    md = {
        "AmSC.common.type": "scientificWork",
        "AmSC.common.description": e["description"].strip(),
        "AmSC.common.display_name": e.get("display_name", e["name"]),
        "lqcd.artifact.kind": "gauge_ensemble",
        "lqcd.ensemble": e["name"],
        "lqcd.collaboration": e.get("collaboration", cfg["defaults"].get("collaboration", "")),
        "lqcd.generated_by": e.get("generated_by", ""),
        "lqcd.machine": e.get("machine", ""),
        "lqcd.code": e.get("code", ""),
        "lqcd.year": e.get("year", ""),
        "lqcd.streams": list(ens["streams"].keys()),
        "lqcd.n_streams": len(ens["streams"]),
        "lqcd.n_configs": sum(s["n_configs"] for s in streams_summary.values()),
        "lqcd.catalog.schema_version": 1,
        "lqcd.catalog.updated_at": utcnow(),
    }
    md.update(flatten(ens["lqcd"], "lqcd"))
    complete_mass_kappa(md)
    return md


def file_metadata(ens: dict, cfg: dict, storage: dict, stream_label: str, stream: dict,
                  f: dict, rule: dict, groups: dict, ens_md: dict) -> dict:
    e = ens["ensemble"]
    name = e["name"]
    kind = rule["kind"]
    traj = int(groups["trajectory"]) if "trajectory" in groups else None
    hpss_path = f["path"]

    what = {
        "gauge_config": f"Gauge configuration, trajectory {traj}",
        "hmc_input": f"HMC restart input XML for trajectory {traj}",
        "hmc_log_archive": f"HMC log/measurement tarball through trajectory {traj}",
    }.get(kind, f"{kind} file" + (f", trajectory {traj}" if traj is not None else ""))
    # Short physics summary from whatever ensemble fields exist (all optional).
    bits = []
    if "lqcd.nflavors" in ens_md:
        bits.append(f"Nf={ens_md['lqcd.nflavors']}")
    if "lqcd.action.fermion" in ens_md:
        bits.append(str(ens_md["lqcd.action.fermion"]))
    if "lqcd.Nx" in ens_md and "lqcd.Nt" in ens_md:
        bits.append(f"{ens_md['lqcd.Nx']}^3 x {ens_md['lqcd.Nt']}")
    if "lqcd.beta" in ens_md:
        bits.append(f"beta={ens_md['lqcd.beta']}")
    if "lqcd.mass.light" in ens_md:
        bits.append(f"m_l={ens_md['lqcd.mass.light']}")
    if "lqcd.kappa.light" in ens_md:
        bits.append(f"kappa_l={ens_md['lqcd.kappa.light']}")
    summary = (": " + ", ".join(bits)) if bits else ""
    description = (
        f"{what} of stream '{stream_label}' of ensemble {name}{summary}. {rule.get('description', '')} "
        f"Stored on {storage['site']} {storage['system'].upper()} at {hpss_path} "
        f"(Globus collection '{storage['globus_label']}')."
    ).strip()

    md = {
        "AmSC.common.type": "artifact",
        "AmSC.common.description": description,
        "AmSC.common.display_name": f"{name} {stream_label} {kind} {traj if traj is not None else ''}".strip(),
        "AmSC.common.location": f"globus://{storage['globus_collection']}{hpss_path}",
        "AmSC.artifact.format": rule.get("format", "application/octet-stream"),
        "lqcd.artifact.kind": kind,
        "lqcd.stream": stream_label,
        "lqcd.stream_dir": stream["dir"],
        "lqcd.storage.system": storage["system"],
        "lqcd.storage.site": storage["site"],
        "lqcd.storage.path": hpss_path,
        "lqcd.storage.mtime": f["mtime"],
        "lqcd.storage.owner": f["owner"],
        "lqcd.storage.globus_collection": storage["globus_collection"],
    }
    if traj is not None:
        md["lqcd.trajectory"] = traj
        tau0 = ens_md.get("lqcd.hmc.tau0")
        if tau0:
            md["lqcd.md_time"] = round(traj * tau0, 6)
    # Stream provenance, only when the TOML states it.
    for k in ("parent_stream", "parent_trajectory"):
        if k in stream:
            md[f"lqcd.stream_{k}"] = stream[k]
    # Ensemble-level physics on every file so query hits are self-describing.
    for k, v in ens_md.items():
        if k.startswith("lqcd.") and k not in md and not k.startswith(("lqcd.n_", "lqcd.streams", "lqcd.stream_summary", "lqcd.catalog.")):
            md[k] = v
    return md


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ensemble_toml")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    ap.add_argument("--inventory", help="inventory JSON (default: work/<ensemble>/inventory.json)")
    ap.add_argument("-o", "--output", help="records JSON (default: work/<ensemble>/records.json)")
    ap.add_argument("--kinds", help="comma-separated lqcd.artifact.kind values to include (default: all rules)")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)
    ens = load_ensemble(args.ensemble_toml)
    name = ens["ensemble"]["name"]
    storage = storage_for(cfg, ens)
    wd = work_dir(cfg, name)
    inv = read_json(args.inventory or wd / "inventory.json")
    if inv["ensemble"] != name:
        sys.exit(f"inventory is for {inv['ensemble']}, not {name}")
    want_kinds = set(args.kinds.split(",")) if args.kinds else None

    # First pass: classify, so the dataset record can carry per-stream counts.
    classified = []  # (stream_label, file, rule, groups)
    unmatched = Counter()
    streams_summary = {}
    for label, st in inv["streams"].items():
        n_cfg = 0
        trajs = []
        for f in st["files"]:
            rule, groups = classify(f["rel_path"], ens["files"])
            if rule is None:
                unmatched[f["rel_path"].split("/")[0] if "/" in f["rel_path"] else f["rel_path"]] += 1
                continue
            if want_kinds and rule["kind"] not in want_kinds:
                continue
            classified.append((label, f, rule, groups))
            if rule["kind"] == "gauge_config":
                n_cfg += 1
                trajs.append(int(groups["trajectory"]))
        streams_summary[label] = {
            "n_configs": n_cfg,
            "trajectory_min": min(trajs) if trajs else None,
            "trajectory_max": max(trajs) if trajs else None,
        }

    ens_md = ensemble_metadata(ens, cfg, streams_summary)
    ens_md["lqcd.stream_summary"] = [
        f"{k}: {v['n_configs']} configs, traj {v['trajectory_min']}-{v['trajectory_max']}" for k, v in streams_summary.items()
    ]

    ns = cfg["metacat"]["namespace"]
    records = []
    seen = {}
    for label, f, rule, groups in classified:
        base = f["path"].rsplit("/", 1)[1]
        # Catalog name rule: the basename, prefixed with the stream directory
        # when the basename does not already start with it. NPLQCD config and
        # restart files embed the stream ("<ensemble>-a_cfg_10.lime") and are
        # kept as is; generic names like "log.1590.tar" become
        # "<ensemble>-a_log.1590.tar" so streams cannot collide.
        sdir = ens["streams"][label]["dir"]
        fname = base if base.startswith(sdir) else f"{sdir}_{base}"
        if fname in seen:
            sys.exit(
                f"file name collision within namespace {ns}: {fname}\n  {seen[fname]}\n  {f['path']}\n"
                "Stream directories must produce distinct basenames; rename or extend the naming rule."
            )
        seen[fname] = f["path"]
        md = file_metadata(ens, cfg, storage, label, ens["streams"][label], f, rule, groups, ens_md)
        md["lqcd.storage.filename"] = base
        records.append({"namespace": ns, "name": fname, "size": f["size"], "metadata": md})

    kinds = Counter(r["metadata"]["lqcd.artifact.kind"] for r in records)
    out = {
        "generated_at": utcnow(),
        "ensemble_toml": str(Path(args.ensemble_toml).resolve()),
        "inventory_generated_at": inv["generated_at"],
        "dataset": {"did": dataset_did(cfg, name), "metadata": ens_md},
        "summary": {
            "n_files": len(records),
            "total_bytes": sum(r["size"] for r in records),
            "kinds": dict(kinds),
            "streams": streams_summary,
            "unmatched": dict(unmatched),
        },
        "files": records,
    }
    outp = Path(args.output) if args.output else wd / "records.json"
    write_json(outp, out)

    print(f"{name}: dataset {out['dataset']['did']}")
    print(f"  {len(records)} file records, {human_bytes(out['summary']['total_bytes'])}: "
          + ", ".join(f"{k}={v}" for k, v in kinds.items()))
    for k, v in streams_summary.items():
        print(f"  stream {k:>5}: {v['n_configs']} configs, trajectories {v['trajectory_min']}..{v['trajectory_max']}")
    if unmatched:
        print(f"  skipped (no [[files]] rule): {dict(unmatched)}")
    print(f"wrote {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
