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

Naming rules (FDP): a name must be unique within the namespace and may use
only letters, digits, "-", ".", "_" and "/". A namespace containing the word
"test" is never migrated to the central AmSC catalog. The script enforces the
first two and warns on the third.

Checksums are optional. HPSS stores none for these files and the bytes are
only on tape, so by default records carry no checksums. If you have hashes
(e.g. from FDP's examples/generate_metadata.py or sha256sum on a DTN), pass
--checksums hashes.json ({name: {alg: hex}} or {name: hex}) and they are
included at declare time; attach_checksums.py adds them to existing records
later, including straight from FDP dCache once a copy lives there.
"""

from __future__ import annotations

import argparse
import re
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
    parent = e.get("parent")
    md = {
        "AmSC.common.type": "scientificWork",
        "AmSC.common.description": e["description"].strip(),
        "AmSC.common.display_name": e.get("display_name", e["name"]),
        # A derived dataset says what it holds and which ensemble it belongs to.
        "lqcd.artifact.kind": e.get("kind", "gauge_ensemble" if not parent else "derived_dataset"),
        "lqcd.ensemble": parent or e["name"],
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
    if parent:
        md["lqcd.parent.kind"] = "gauge_ensemble"
        md["lqcd.parent.ensemble"] = parent
        md["lqcd.parent.dataset"] = f'{cfg["metacat"]["namespace"]}:{parent}'
    # Dataset-level facts about a derived product ([derived] table, flattened).
    if ens.get("derived"):
        md.update(flatten(ens["derived"], "lqcd"))
    # Downstream observables ([observables] table): dataset-level only, flattened
    # to lqcd.obs.<name>.<field>. lqcd.obs.list names the registered observables.
    obs = ens.get("observables") or {}
    if obs:
        md.update(flatten(obs, "lqcd.obs"))
        md["lqcd.obs.list"] = [k for k, v in obs.items() if isinstance(v, dict)]
    return md


def physics_summary(ens_md: dict) -> str:
    """Short physics summary from whatever ensemble fields exist (all optional),
    e.g. ": Nf=3, clover, 32^3 x 64, beta=6.5, m_l=-0.1788, kappa_l=0.1308"."""
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
    return (": " + ", ".join(bits)) if bits else ""


# Ensemble-level keys that describe the dataset as a whole and are not copied
# onto every file record (file records get the physics keys, lqcd.beta etc.).
# Ensemble-level fields that stay on the dataset record and are NOT copied onto
# every file (counts, stream bookkeeping, and the downstream observables).
DATASET_ONLY_PREFIXES = ("lqcd.n_", "lqcd.streams", "lqcd.stream_summary", "lqcd.catalog.", "lqcd.obs.")
# FDP naming rule: letters, digits, "-", ".", "_" and "/" only.
NAME_CHARS = "A-Z a-z 0-9 - . _ /"
NAME_OK = re.compile(r"^[A-Za-z0-9._/-]+$")


def describe_counts(s: dict) -> str:
    """'422 configs' | '5 config archives' | '4010 configs in 1 archive' | '1534 files'."""
    n, a = s["n_configs"], s["n_config_archives"]
    if a and n:
        return f"{n} configs in {a} archive{'s' if a != 1 else ''}"
    if a:
        return f"{a} config archive{'s' if a != 1 else ''}"
    if not n and s.get("n_files"):
        return f"{s['n_files']} files"
    return f"{n} configs"


def file_metadata(ens: dict, storage: dict, stream_label: str, stream: dict,
                  f: dict, rule: dict, groups: dict, ens_md: dict) -> dict:
    name = ens["ensemble"]["name"]
    kind = rule["kind"]
    traj = int(groups["trajectory"]) if "trajectory" in groups else None
    # A rule may capture a trajectory *range* instead (archives holding many configs).
    tmin = int(groups["trajectory_min"]) if "trajectory_min" in groups else None
    tmax = int(groups["trajectory_max"]) if "trajectory_max" in groups else None
    hpss_path = f["path"]

    if traj is not None:
        where = f", trajectory {traj}"
    elif tmin is not None and tmax is not None:
        where = f", trajectories {tmin} to {tmax}"
    else:
        where = ""
    what = {
        "gauge_config": f"Gauge configuration{where}",
        "gauge_config_archive": f"Archive of gauge configurations{where}",
        "hmc_input": f"HMC restart input XML for{where.replace(', ', ' ')}",
        "hmc_log_archive": f"HMC log/measurement tarball through{where.replace(', trajectory', ' trajectory')}",
    }.get(kind, f"{kind.replace('_', ' ').capitalize()}{where}")
    ensemble_name = ens["ensemble"].get("parent") or name   # derived products belong to the parent ensemble
    description = (
        f"{what} of stream '{stream_label}' of ensemble {ensemble_name}{physics_summary(ens_md)}. "
        f"{rule.get('description', '')} "
        f"Stored on {storage['site']} {storage['system'].upper()} at {hpss_path} "
        f"(Globus collection '{storage['globus_label']}')."
    ).strip()

    md = {
        "AmSC.common.type": "artifact",
        "AmSC.common.description": description,
        "AmSC.common.display_name": f"{name} {stream_label} {kind}{where.replace(', trajectory', '').replace('trajectories ', '')}".strip(),
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
    tau0 = ens_md.get("lqcd.hmc.tau0")
    if traj is not None:
        md["lqcd.trajectory"] = traj
        if tau0:
            md["lqcd.md_time"] = round(traj * tau0, 6)
    if tmin is not None and tmax is not None:
        md["lqcd.trajectory_min"] = tmin
        md["lqcd.trajectory_max"] = tmax
        if tau0:
            md["lqcd.md_time_min"] = round(tmin * tau0, 6)
            md["lqcd.md_time_max"] = round(tmax * tau0, 6)
    if "n_members" in groups:  # from an htar index (build_records.py --archive-index)
        md["lqcd.n_configs_in_archive"] = int(groups["n_members"])
    # Any other named regex group becomes lqcd.<rule.group_prefix>.<group>
    # (default prefix "prop"), e.g. (?P<tsrc>\d+) -> lqcd.prop.tsrc.
    reserved = {"trajectory", "trajectory_min", "trajectory_max", "stream", "n_members"}
    gprefix = rule.get("group_prefix", "prop")
    for g, v in groups.items():
        if g not in reserved:
            md[f"lqcd.{gprefix}.{g}"] = int(v) if str(v).isdigit() else v
    # Rule-level constant metadata ([files.meta] in the TOML), keys given in full.
    for k, v in (rule.get("meta") or {}).items():
        md[k] = v
    # Derived products point back at the configuration they were computed on.
    parent = ens["ensemble"].get("parent")
    if parent:
        md["lqcd.parent.kind"] = "gauge_config"
        md["lqcd.parent.ensemble"] = parent
        md["lqcd.parent.stream"] = stream_label
        if traj is not None:
            md["lqcd.parent.trajectory"] = traj
        tmpl = rule.get("parent_name") or ens["ensemble"].get("parent_name")
        if tmpl and traj is not None:
            fields = dict(groups)
            fields.update(stream_dir=stream["dir"].rsplit("/", 1)[-1], stream=stream_label, trajectory=traj)
            pname = tmpl.format(**fields)
            md["lqcd.parent.name"] = pname
            md["lqcd.parent.did"] = f"{ens['_ns']}:{pname}"
    # Stream provenance, only when the TOML states it.
    for k in ("parent_stream", "parent_trajectory"):
        if k in stream:
            md[f"lqcd.stream_{k}"] = stream[k]
    # Ensemble-level physics on every file so query hits are self-describing.
    for k, v in ens_md.items():
        if k.startswith("lqcd.") and k not in md and not k.startswith(DATASET_ONLY_PREFIXES):
            md[k] = v
    return md


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ensemble_toml")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    ap.add_argument("--inventory", help="inventory JSON (default: work/<ensemble>/inventory.json)")
    ap.add_argument("-o", "--output", help="records JSON (default: work/<ensemble>/records.json)")
    ap.add_argument("--kinds", help="comma-separated lqcd.artifact.kind values to include (default: all rules)")
    ap.add_argument("--archive-index", metavar="JSON",
                    help="htar_members.py output; gives gauge_config_archive records their trajectory range and "
                         "member count when the archive name itself does not carry them")
    ap.add_argument("--checksums", metavar="JSON", help="optional {name: {alg: hex}} or {name: hex} to include")
    ap.add_argument("--checksum-algorithm", default="sha256", help="algorithm for bare-string --checksums values")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)
    ens = load_ensemble(args.ensemble_toml)
    name = ens["ensemble"]["name"]
    storage = storage_for(cfg, ens)
    wd = work_dir(cfg, name)
    parent = ens["ensemble"].get("parent")
    inv_path = args.inventory or wd / "inventory.json"
    if not Path(inv_path).exists() and parent:
        inv_path = work_dir(cfg, parent) / "inventory.json"   # derived datasets reuse the parent's listing
    inv = read_json(inv_path)
    if inv["ensemble"] not in (name, parent):
        sys.exit(f"inventory is for {inv['ensemble']}, not {name}")
    ens["_ns"] = cfg["metacat"]["namespace"]
    want_kinds = set(args.kinds.split(",")) if args.kinds else None

    # Optional htar index (htar_members.py output): archive path -> trajectory
    # range + member count, for archives whose names do not say what they hold.
    archive_index = {}
    if args.archive_index:
        for arc, v in read_json(args.archive_index)["archives"].items():
            archive_index[arc] = {"trajectory_min": v["trajectory_min"], "trajectory_max": v["trajectory_max"],
                                  "n_members": v["n_members"]}

    # First pass: classify, so the dataset record can carry per-stream counts.
    # A rule may capture a (?P<stream>...) group; that overrides the directory's
    # stream label for the file (several streams archived in one directory).
    classified = []  # (stream_label, directory_stream_label, file, rule, groups)
    unmatched = Counter()
    per_stream: dict[str, dict] = {}
    excluded = Counter()
    for dir_label, st in inv["streams"].items():
        if dir_label not in ens["streams"]:
            # A derived dataset may cover only some of the parent's streams; the
            # parent's inventory still lists all of them.
            continue
        # [streams.<x>].exclude = ['regex', ...]: files in this stream's directory
        # to leave out (e.g. a copy of another stream's configuration kept as
        # the branch point).
        excludes = [re.compile(p) for p in ens["streams"][dir_label].get("exclude", [])]
        for f in st["files"]:
            if any(p.search(f["rel_path"]) for p in excludes):
                excluded[dir_label] += 1
                continue
            rule, groups = classify(f["rel_path"], ens["files"])
            if rule is None:
                unmatched[f["rel_path"].split("/")[0]] += 1  # count by top-level directory
                continue
            if want_kinds and rule["kind"] not in want_kinds:
                continue
            if f["path"] in archive_index:
                groups = dict(groups, **{k: str(v) for k, v in archive_index[f["path"]].items() if v is not None})
            label = groups.get("stream", dir_label)
            classified.append((label, dir_label, f, rule, groups))
            s = per_stream.setdefault(label, {"n_configs": 0, "n_config_archives": 0, "n_files": 0,
                                              "trajs": [], "other_trajs": []})
            s["n_files"] += 1
            if rule["kind"] not in ("gauge_config", "gauge_config_archive"):
                # used only when a stream has no configs (derived datasets)
                if "trajectory" in groups:
                    s["other_trajs"].append(int(groups["trajectory"]))
                if "trajectory_min" in groups and "trajectory_max" in groups:
                    s["other_trajs"] += [int(groups["trajectory_min"]), int(groups["trajectory_max"])]
            if rule["kind"] == "gauge_config":
                s["n_configs"] += 1
                s["trajs"].append(int(groups["trajectory"]))
            elif rule["kind"] == "gauge_config_archive":
                s["n_config_archives"] += 1
                if "trajectory_min" in groups:
                    s["trajs"] += [int(groups["trajectory_min"]), int(groups["trajectory_max"])]
                if "n_members" in groups:
                    s["n_configs"] += int(groups["n_members"])
    streams_summary = {
        label: {
            "n_configs": s["n_configs"],
            "n_config_archives": s["n_config_archives"],
            "n_files": s["n_files"],
            "trajectory_min": min(s["trajs"] or s["other_trajs"]) if (s["trajs"] or s["other_trajs"]) else None,
            "trajectory_max": max(s["trajs"] or s["other_trajs"]) if (s["trajs"] or s["other_trajs"]) else None,
        }
        for label, s in per_stream.items()
    }
    # Streams discovered from file names (not listed in the TOML) inherit the
    # directory stream's entry so the dataset record and file records see them.
    for label, dir_label, *_ in classified:
        if label not in ens["streams"]:
            ens["streams"][label] = dict(ens["streams"][dir_label], derived_from_dir=dir_label)
    for label in [s for s in ens["streams"] if s not in per_stream]:
        sys.stderr.write(f"[warn] stream {label!r} has no matching files; not listed on the dataset\n")
        del ens["streams"][label]

    ens_md = ensemble_metadata(ens, cfg, streams_summary)
    if any(v["n_config_archives"] for v in streams_summary.values()):
        ens_md["lqcd.n_config_archives"] = sum(v["n_config_archives"] for v in streams_summary.values())
    ens_md["lqcd.stream_summary"] = [
        f"{k}: {describe_counts(v)}, traj {v['trajectory_min']}-{v['trajectory_max']}" for k, v in streams_summary.items()
    ]

    ns = cfg["metacat"]["namespace"]
    if "test" in ns:
        sys.stderr.write(f"[warn] namespace {ns!r} contains 'test': FDP never migrates such namespaces "
                         "to the central AmSC catalog\n")
    given_checksums = read_json(args.checksums) if args.checksums else {}
    records = []
    seen = {}
    for label, dir_label, f, rule, groups in classified:
        base = f["path"].rsplit("/", 1)[1]
        # Catalog name rule: the basename, prefixed with the stream directory's
        # last path component when the basename does not already contain it.
        # NPLQCD config, restart and log files embed the stream
        # ("<ensemble>-a_cfg_10.lime", "logs_<ensemble>_a.tar") and are kept as
        # is; generic names like "log.1590.tar" become "<ensemble>-a_log.1590.tar"
        # so streams cannot collide. The collision check below is the backstop.
        sdir = ens["streams"][label]["dir"].rstrip("/").rsplit("/", 1)[-1]
        if ens["ensemble"].get("name_style") == "path":
            # "<stream dir>/<path inside it>": for derived products whose
            # basenames repeat across subdirectories (sparseprops-t24/1000.tar).
            fname = f"{sdir}/{f['rel_path']}"
        else:
            fname = base if sdir in base else f"{sdir}_{base}"
        if not NAME_OK.match(fname):
            sys.exit(f"catalog name {fname!r} (from {f['path']}) uses characters outside FDP's allowed set "
                     f"{NAME_CHARS}; rename the file or extend the naming rule")
        if fname in seen:
            sys.exit(
                f"file name collision within namespace {ns}: {fname}\n  {seen[fname]}\n  {f['path']}\n"
                "Stream directories must produce distinct basenames; rename or extend the naming rule."
            )
        seen[fname] = f["path"]
        md = file_metadata(ens, storage, label, ens["streams"][label], f, rule, groups, ens_md)
        md["lqcd.storage.filename"] = base
        rec = {"namespace": ns, "name": fname, "size": f["size"], "metadata": md}
        cs = given_checksums.get(fname) or given_checksums.get(base)
        if cs:
            rec["checksums"] = {args.checksum_algorithm: cs} if isinstance(cs, str) else dict(cs)
        records.append(rec)

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
        print(f"  stream {k:>5}: {describe_counts(v)}, trajectories {v['trajectory_min']}..{v['trajectory_max']}")
    if unmatched:
        print(f"  skipped (no [[files]] rule): {dict(unmatched)}")
    if excluded:
        print(f"  excluded by [streams.*].exclude: {dict(excluded)}")
    print(f"wrote {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
