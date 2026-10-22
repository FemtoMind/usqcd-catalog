#!/usr/bin/env python3
"""
Register a new ensemble (or derived dataset) in the catalog from a hand-written
description, the one time a TOML file is written by hand.

    register_ensemble.py my_new_ensemble.toml              # start from templates/ensemble.toml
    register_ensemble.py my_sparseprops.toml               # derived: [ensemble] parent = "<ensemble>"
    register_ensemble.py my_new_ensemble.toml --dry-run    # every check, no write

Checks, all before anything is written:
  * the name is new in the namespace: ensemble names are permanent, so an
    existing one is refused (with who registered it) and you are asked to
    pick another; names use letters, digits, ".", "-" and "_";
  * stream labels are valid and no two streams share a directory;
  * a derived dataset's parent is registered.

What it writes: the dataset record with the physics, observables, file rules
and streams (registered by you, now). The file's '#' comments become
[ensemble] provenance, so nothing written in them is lost. The registry
snapshot ensembles/<name>.toml (or derived_datasets/<name>.toml) is then
generated from the catalog; that is the copy to commit, your input file can
be deleted. Then catalog the files with inventory -> build -> declare -> diff.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_records import ensemble_metadata  # noqa: E402
from catalog_common import load_catalog_config, load_ensemble, metacat_client, utcnow  # noqa: E402
from registry import (  # noqa: E402
    LABEL_OK, comments_to_provenance, new_revision, snapshot_path,
)
from sync_registry import sync_one  # noqa: E402

NAME_OK = re.compile(r"^[A-Za-z0-9._-]+$")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("description", help="hand-written TOML (see templates/)")
    ap.add_argument("--dry-run", action="store_true", help="run every check, write nothing")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)
    ns = cfg["metacat"]["namespace"]
    client = metacat_client(cfg, need_auth=not args.dry_run)
    user = client.auth_info()[0] if not args.dry_run else "(you)"

    text = Path(args.description).read_text()
    raw = tomllib.loads(text)
    e = raw.get("ensemble") or {}
    name = e.get("name")
    if not name:
        sys.exit("[ensemble] name is required")
    if not NAME_OK.match(name):
        sys.exit(f"name {name!r}: use letters, digits, '.', '-' and '_' only")
    did = f"{ns}:{name}"
    ds = client.get_dataset(did)
    if ds is not None:
        sys.exit(f"{did} is already registered (by {ds.get('creator', '?')}). Ensemble names are permanent; "
                 "pick a different name. To change that ensemble, use add_stream.py or update_ensemble.py.")
    parent = e.get("parent")
    if parent:
        pds = client.get_dataset(f"{ns}:{parent}")
        if pds is None:
            sys.exit(f"parent ensemble {parent!r} is not registered; register it first.")
        sync_one(client, ns, parent, quiet=True)        # the build below reads the parent's snapshot
    streams = raw.get("streams") or {}
    for label, st in streams.items():
        if not LABEL_OK.match(label):
            sys.exit(f"stream label {label!r}: use letters, digits, '-' and '_' only")
    dirs = [st.get("dir", name if l == "main" else f"{name}-{l}") for l, st in streams.items()]
    if len(set(dirs)) != len(dirs):
        sys.exit(f"two streams share a directory: {dirs}")

    # Comments -> [ensemble] provenance; streams registered by this user, now.
    prov = comments_to_provenance(text)
    ens_tbl = {"name": name}
    if prov or e.get("provenance"):
        ens_tbl["provenance"] = (e.get("provenance", "") + ("\n" if e.get("provenance") and prov else "") + prov)
    ens_tbl.update({k: v for k, v in e.items() if k not in ens_tbl})
    raw["ensemble"] = ens_tbl
    now = utcnow()
    for st in streams.values():
        st.setdefault("registered_by", user)
        st.setdefault("registered_at", now)

    ens = load_ensemble(args.description)       # fills defaults (streams, file rules, parent physics)
    md = ensemble_metadata(ens, cfg, {}, raw=raw)
    md["lqcd.catalog.revision"] = new_revision(user)
    if args.dry_run:
        print(f"[dry-run] all checks passed; would create {did} with {len(md)} metadata fields, "
              f"{len(streams) or 'inherited'} stream(s), provenance {len(prov)} chars")
        print(f"          snapshot would be written to {snapshot_path(name, raw)}")
        return 0
    client.create_dataset(did, metadata=md, description=md.get("AmSC.common.display_name", ""))
    sync_one(client, ns, name, quiet=True)
    print(f"registered {did}; snapshot written to {snapshot_path(name, raw)} (commit that file; "
          f"{args.description} is no longer needed).")
    print("Next, catalog its files:")
    sub = "derived_datasets" if parent else "ensembles"
    if not parent:      # derived datasets reuse the parent's inventory
        print(f"  python3 scripts/hpss_inventory.py  {sub}/{name}.toml        # or write work/{name}/inventory.json")
    print(f"  python3 scripts/build_records.py   {sub}/{name}.toml")
    print(f"  python3 scripts/declare_records.py work/{name}/records.json --limit 5")
    print(f"  python3 scripts/retrieve_records.py {name} --diff work/{name}/records.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
