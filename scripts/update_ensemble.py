#!/usr/bin/env python3
"""
Add or change physics information on a registered ensemble (or derived
dataset): parameters, observables, description text, file rules.

    update_ensemble.py --ensemble cl3_48_96_b6p5_m0p1788 --set lqcd.hmc.n_steps=16
    update_ensemble.py --ensemble cl3_48_96_b6p5_m0p1788 \
        --set observables.mpi.value=0.3042 --set observables.mpi.stat=0.0001
    update_ensemble.py --ensemble cl3_48_96_b6p5_m0p1788 --merge new_observables.toml
    ... --dry-run                      # show the change, write nothing

--set KEY=VALUE   KEY is a dotted path into the description, the same names
                  as the TOML tables: lqcd.*, observables.*, derived.*,
                  ensemble.description, ... VALUE is read as a TOML value
                  (16, 0.3042, true, [1, 2], "text"); anything that is not
                  valid TOML is taken as a plain string.
--merge FILE      a TOML fragment merged table by table into the description,
                  e.g. a whole [observables.w0] table, or [[files]] (arrays
                  replace the existing array).

The script starts from the catalog's current state (no local file to keep
in sync), so two people updating different fields do not overwrite each
other. Refused: changes under [streams] (use add_stream.py; labels are
permanent), the [ensemble] name, and an ensemble registered by someone else
unless --force. Afterwards the local snapshot is regenerated. If the change
touches physics that file records also carry, the script says so: rebuild
and declare with --update-existing to propagate it to the files.
"""

from __future__ import annotations

import argparse
import copy
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_records import DATASET_ONLY_PREFIXES, ensemble_metadata  # noqa: E402
from catalog_common import load_catalog_config, load_ensemble, metacat_client  # noqa: E402
from registry import (  # noqa: E402
    REGISTRY_TABLE, check_owner, check_schema, description_from_metadata, new_revision, snapshot_path,
    snapshot_text,
)
from sync_registry import sync_one  # noqa: E402

COUNT_PREFIXES = ("lqcd.n_", "lqcd.stream_summary", "lqcd.stream_labels")


def parse_value(text: str):
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def set_path(d: dict, path: str, value) -> None:
    keys = path.split(".")
    for k in keys[:-1]:
        if not isinstance(d.setdefault(k, {}), dict):
            sys.exit(f"--set {path}: {k!r} is a value, not a table")
        d = d[k]
    d[keys[-1]] = value


def merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ensemble", required=True, help="registered ensemble or derived dataset name")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="repeatable")
    ap.add_argument("--merge", action="append", default=[], metavar="FILE.toml", help="repeatable")
    ap.add_argument("--force", action="store_true", help="an ensemble registered by someone else")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    args = ap.parse_args()
    if not args.set and not args.merge:
        ap.error("nothing to change: give --set KEY=VALUE and/or --merge FILE.toml")

    cfg = load_catalog_config(args.config)
    ns = cfg["metacat"]["namespace"]
    client = metacat_client(cfg, need_auth=not args.dry_run)
    user = client.auth_info()[0] if not args.dry_run else "(you)"
    name = args.ensemble
    did = f"{ns}:{name}"
    ds = client.get_dataset(did)
    if ds is None:
        sys.exit(f"{did} is not registered (see retrieve_records.py --summary); "
                 "new ensembles go through register_ensemble.py.")
    remote = ds.get("metadata") or {}
    check_schema(remote, did)
    if not args.dry_run:
        check_owner(ds, user, args.force, did)
    if "lqcd.catalog.source" not in remote:
        sys.exit(f"{did} predates the catalog registry; run scripts/migrations/registry_v2.py first.")

    before = description_from_metadata(remote)
    raw = copy.deepcopy(before)
    for item in args.set:
        if "=" not in item:
            sys.exit(f"--set {item!r}: expected KEY=VALUE")
        key, val = item.split("=", 1)
        key = key.strip()
        if key.split(".")[0] in ("streams", REGISTRY_TABLE) or key == "ensemble.name":
            sys.exit(f"--set {key}: streams are added with add_stream.py and names are permanent")
        set_path(raw, key, parse_value(val.strip()))
    for f in args.merge:
        frag = tomllib.loads(Path(f).read_text())
        if "streams" in frag or REGISTRY_TABLE in frag or "name" in frag.get("ensemble", {}):
            sys.exit(f"{f}: may not contain [streams], [{REGISTRY_TABLE}] or [ensemble] name")
        merge(raw, frag)
    if raw == before:
        print(f"{did}: nothing to change")
        return 0

    # Build the dataset metadata from the updated description, through the
    # same code path as build_records (via a temporary snapshot file).
    path = snapshot_path(name, raw)
    tmp = path.with_suffix(".update.toml")
    tmp.write_text(snapshot_text(name, ns, raw))
    try:
        new = ensemble_metadata(load_ensemble(tmp), cfg, {}, raw=tomllib.loads(tmp.read_text()))
    finally:
        tmp.unlink()
    new = {k: v for k, v in new.items() if not k.startswith(COUNT_PREFIXES)}
    changed = {k: v for k, v in new.items() if remote.get(k) != v and k != "lqcd.catalog.updated_at"}
    file_level = sorted(k for k in changed if k.startswith("lqcd.") and not k.startswith(DATASET_ONLY_PREFIXES))
    shown = sorted(k for k in changed if k != "lqcd.catalog.source")
    print(f"{did}: {len(shown)} catalog field(s) change: " + (", ".join(shown[:12]) or "(description only)")
          + (" ..." if len(shown) > 12 else ""))
    if args.dry_run:
        if file_level:
            print(f"(file records also carry {', '.join(file_level[:6])}; after the update, rebuild and "
                  "declare with --update-existing to propagate)")
        return 0
    new["lqcd.catalog.revision"] = new_revision(user)
    client.update_dataset(did, metadata=new, mode="update")
    sync_one(client, ns, name, quiet=True)
    print(f"updated {did}; local snapshot regenerated.")
    if file_level:
        sub = path.parent.name
        print(f"File records also carry {', '.join(file_level[:6])}{' ...' if len(file_level) > 6 else ''}; "
              f"propagate with build_records.py {sub}/{name}.toml, then "
              f"declare_records.py work/{name}/records.json --update-existing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
