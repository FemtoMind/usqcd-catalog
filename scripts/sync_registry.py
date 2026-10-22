#!/usr/bin/env python3
"""
Write the registry snapshots (ensembles/*.toml, derived_datasets/*.toml) from
the catalog, which is the source of truth.

    sync_registry.py                         # every registered dataset in the namespace
    sync_registry.py --ensemble cl3_48_96_b6p5_m0p1788 [--ensemble ...]
    sync_registry.py --check                 # exit 1 if any snapshot differs from the catalog

Anonymous (reads only). Output is deterministic: the same catalog state gives
byte-identical files, so `git diff` after a sync shows exactly what changed in
the catalog and two people syncing never conflict. If git does report a
conflict in a snapshot, resolve it by running this script, never by hand.

The registry scripts (add_stream.py, register_ensemble.py, declare_records.py)
call this for the dataset they changed, so a local checkout is current after
every write.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import load_catalog_config, metacat_client  # noqa: E402
from registry import check_schema, description_from_metadata, snapshot_path, snapshot_text  # noqa: E402


def sync_one(client, ns: str, name: str, check: bool = False, quiet: bool = False) -> str:
    """Write (or with check=True, compare) the snapshot of lqcd:<name>.
    Returns 'written', 'unchanged', 'differs', 'missing' or 'legacy'."""
    ds = client.get_dataset(f"{ns}:{name}")
    if ds is None:
        return "missing"
    md = ds.get("metadata") or {}
    check_schema(md, f"{ns}:{name}")
    try:
        raw = description_from_metadata(md)
    except ValueError:
        return "legacy"
    path = snapshot_path(name, raw)
    text = snapshot_text(name, ns, raw)
    old = path.read_text() if path.exists() else None
    if old == text:
        return "unchanged"
    if check:
        return "differs"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if not quiet:
        print(f"wrote {path.relative_to(path.parents[1])}")
    return "written"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ensemble", action="append", help="dataset name (repeatable); default: all")
    ap.add_argument("--check", action="store_true", help="compare only; exit 1 if a snapshot is stale")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)
    ns = cfg["metacat"]["namespace"]
    client = metacat_client(cfg, need_auth=False)
    names = args.ensemble or sorted(d["name"] for d in client.list_datasets(namespace_pattern=ns))
    counts: dict[str, list] = {}
    for n in names:
        counts.setdefault(sync_one(client, ns, n, check=args.check, quiet=args.check), []).append(n)
    for state in ("written", "differs", "missing", "legacy"):
        if counts.get(state):
            print(f"{state}: {', '.join(counts[state])}")
    print(f"{len(names)} datasets: " + ", ".join(f"{len(v)} {k}" for k, v in sorted(counts.items())))
    if counts.get("legacy"):
        print("legacy = written before registry schema 2 (no lqcd.catalog.source); "
              "see scripts/migrations/registry_v2.py")
    return 1 if (args.check and counts.get("differs")) or counts.get("missing") else 0


if __name__ == "__main__":
    sys.exit(main())
