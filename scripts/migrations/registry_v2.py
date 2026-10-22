#!/usr/bin/env python3
"""
One-off migration to registry schema 2 (the catalog becomes the registry).

For every dataset that has a hand-written description in ensembles/ or
derived_datasets/:
  1. the description's '#' comments become [ensemble] provenance (nothing lost);
  2. each [streams.<label>] gets registered_by / registered_at from the
     dataset's creator and creation time;
  3. the dataset record gets the registry fields (lqcd.catalog.source,
     lqcd.streams.<label>.*, schema_version 2, a revision), the old
     lqcd.streams list is renamed lqcd.stream_labels (same values), and the
     record is written back with every other field untouched;
  4. the local description is replaced by the generated snapshot.

    migrations/registry_v2.py --dry-run          # show what would change
    migrations/registry_v2.py [--only NAME ...]   # migrate

File records are not touched. Re-running skips datasets already at schema 2.
"""

from __future__ import annotations

import argparse
import sys
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog_common import REPO_ROOT, load_catalog_config, metacat_client  # noqa: E402
from registry import (  # noqa: E402
    SCHEMA_VERSION, comments_to_provenance, new_revision, registry_fields,
)
from sync_registry import sync_one  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", help="dataset names to migrate (default: all with a description)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_catalog_config()
    ns = cfg["metacat"]["namespace"]
    client = metacat_client(cfg, need_auth=not args.dry_run)
    user = client.auth_info()[0] if not args.dry_run else "(dry-run)"
    paths = {p.stem: p for d in ("ensembles", "derived_datasets") for p in sorted((REPO_ROOT / d).glob("*.toml"))}
    names = args.only or sorted(paths)
    done = skipped = 0
    for name in names:
        if name not in paths:
            print(f"{name}: no local description, skipped")
            continue
        ds = client.get_dataset(f"{ns}:{name}")
        if ds is None:
            print(f"{name}: not in the catalog, skipped")
            continue
        md = dict(ds.get("metadata") or {})
        if md.get("lqcd.catalog.schema_version") == SCHEMA_VERSION and "lqcd.catalog.source" in md:
            skipped += 1
            continue
        text = paths[name].read_text()
        raw = tomllib.loads(text)
        prov = comments_to_provenance(text)
        ens = {"name": raw["ensemble"]["name"]}
        if prov:
            ens["provenance"] = prov
        ens.update({k: v for k, v in raw["ensemble"].items() if k not in ens})
        raw["ensemble"] = ens
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ds.get("created_timestamp") or time.time()))
        for st in (raw.get("streams") or {}).values():
            st.setdefault("registered_by", ds.get("creator") or user)
            st.setdefault("registered_at", when)
        new = {k: v for k, v in md.items() if k != "lqcd.streams"}
        new.update(registry_fields(raw))
        new["lqcd.stream_labels"] = md.get("lqcd.streams") or md.get("lqcd.stream_labels") or list(raw.get("streams", {}))
        new["lqcd.catalog.revision"] = new_revision(user)
        added = sorted(set(new) - set(md))
        removed = sorted(set(md) - set(new))
        print(f"{name}: +{len(added)} keys ({', '.join(a for a in added if not a.startswith('lqcd.streams.'))}"
              f"{', lqcd.streams.<label>.* x' + str(sum(a.startswith('lqcd.streams.') for a in added)) if any(a.startswith('lqcd.streams.') for a in added) else ''})"
              f", -{removed}, provenance {len(prov)} chars")
        if args.dry_run:
            continue
        client.update_dataset(f"{ns}:{name}", metadata=new, mode="replace")
        state = sync_one(client, ns, name, quiet=True)
        if state not in ("written", "unchanged"):
            sys.exit(f"{name}: migrated but snapshot sync returned {state}")
        done += 1
    print(f"migrated {done}, already at schema {SCHEMA_VERSION}: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
