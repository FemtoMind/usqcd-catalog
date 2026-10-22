#!/usr/bin/env python3
"""
Push a change to a registered ensemble's description (physics, observables,
description text, provenance, file rules) to the catalog.

    python3 scripts/sync_registry.py --ensemble <name>      # start from the current catalog state
    $EDITOR ensembles/<name>.toml                            # change what needs changing
    python3 scripts/update_ensemble.py ensembles/<name>.toml [--dry-run]

Refuses, before writing anything:
  * if the catalog changed since your snapshot was synced (someone else wrote
    to it): sync, re-apply your edit, re-run;
  * changes under [streams] that add or remove a stream (use add_stream.py;
    stream labels are permanent) or rewrite who registered it; moving a
    stream's directory needs --force;
  * an ensemble registered by someone else, unless --force.
Counts and stream summaries are left as the last declare wrote them. Fields
deleted from the TOML are not deleted from the catalog (MetaCat merges).
If ensemble physics that file records also carry changed, the script says so:
rebuild and run declare_records.py --update-existing to propagate it.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_records import DATASET_ONLY_PREFIXES, ensemble_metadata  # noqa: E402
from catalog_common import load_catalog_config, load_ensemble, metacat_client  # noqa: E402
from registry import (  # noqa: E402
    check_owner, check_revision, check_schema, new_revision, streams_from_metadata,
)
from sync_registry import sync_one  # noqa: E402

COUNT_PREFIXES = ("lqcd.n_", "lqcd.stream_summary", "lqcd.stream_labels")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("description", help="the edited snapshot, ensembles/<name>.toml or derived_datasets/<name>.toml")
    ap.add_argument("--force", action="store_true", help="another group's ensemble, or a stream directory move")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)
    ns = cfg["metacat"]["namespace"]
    client = metacat_client(cfg, need_auth=True)
    user = client.auth_info()[0]
    raw = tomllib.loads(Path(args.description).read_text())
    name = (raw.get("ensemble") or {}).get("name")
    if not name or Path(args.description).stem != name:
        sys.exit(f"[ensemble] name {name!r} must match the file name; ensemble names cannot be changed.")
    did = f"{ns}:{name}"
    ds = client.get_dataset(did)
    if ds is None:
        sys.exit(f"{did} is not registered; new ensembles go through register_ensemble.py.")
    remote = ds.get("metadata") or {}
    check_schema(remote, did)
    check_revision(remote, (raw.get("catalog") or {}).get("revision"), args.force, did, name)
    check_owner(ds, user, args.force, did)

    # Streams: only parent/note edits here; additions via add_stream.py.
    if not remote.get("lqcd.catalog.inherits_streams"):
        have, want = streams_from_metadata(remote), raw.get("streams") or {}
        if set(want) - set(have):
            sys.exit(f"new streams {sorted(set(want) - set(have))}: register them with add_stream.py.")
        if set(have) - set(want):
            sys.exit(f"streams {sorted(set(have) - set(want))} are missing from your file; stream labels are "
                     "permanent and cannot be removed.")
        for label, st in want.items():
            for f in ("registered_by", "registered_at"):
                if st.get(f) != have[label].get(f):
                    sys.exit(f"stream {label!r}: {f} cannot be edited.")
            if st.get("dir") != have[label].get("dir") and not args.force:
                sys.exit(f"stream {label!r}: directory change {have[label].get('dir')!r} -> {st.get('dir')!r} "
                         "means the data moved; re-run with --force if so (copies elsewhere are replicas, "
                         "not a new directory).")

    ens = load_ensemble(args.description)
    new = ensemble_metadata(ens, cfg, {}, raw=raw)
    new = {k: v for k, v in new.items() if not k.startswith(COUNT_PREFIXES)}
    changed = {k: v for k, v in new.items() if remote.get(k) != v and k != "lqcd.catalog.updated_at"}
    if not changed:
        print(f"{did}: nothing to change")
        return 0
    file_level = sorted(k for k in changed if k.startswith("lqcd.") and not k.startswith(DATASET_ONLY_PREFIXES))
    print(f"{did}: {len(changed)} field(s) change: " + ", ".join(sorted(changed)[:12])
          + (" ..." if len(changed) > 12 else ""))
    if args.dry_run:
        return 0
    new["lqcd.catalog.revision"] = new_revision(user)
    client.update_dataset(did, metadata=new, mode="update")
    sync_one(client, ns, name, quiet=True)
    print(f"updated {did}; snapshot regenerated from the catalog.")
    if file_level:
        print(f"File records also carry {', '.join(file_level[:6])}{' ...' if len(file_level) > 6 else ''}; "
              f"propagate with: build_records.py {args.description} && "
              f"declare_records.py work/{name}/records.json --update-existing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
