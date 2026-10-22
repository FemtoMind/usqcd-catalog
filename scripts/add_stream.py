#!/usr/bin/env python3
"""
Register a new Markov chain stream of an existing ensemble in the catalog.

    add_stream.py --ensemble cl3_48_96_b6p5_m0p1788 --stream b
    add_stream.py --ensemble cl21_48_96_b6p3_m0p2416_m0p2050 --stream 3000 \
        --dir cl21_48_96_b6p3_m0p2416_m0p2050-3000 --parent-stream main --parent-trajectory 3000
    add_stream.py ... --dry-run           # all checks, no write

No file editing: the stream becomes lqcd.streams.<label>.* keys on the
ensemble's dataset record, and the local snapshot (ensembles/<name>.toml) is
regenerated from the catalog before and after. Then catalog the stream's files
with the usual inventory -> build -> declare -> diff (printed at the end).

Checks, all before anything is written:
  * the ensemble exists in the catalog (else: register_ensemble.py);
  * the label is new: stream labels are permanent, so an existing one is
    refused and you are asked to pick another; labels are letters, digits,
    "-" and "_";
  * no other stream of the ensemble uses the same directory;
  * --parent-stream, if given, is a stream of the ensemble;
  * the ensemble was registered by you, or --force (another group's ensemble:
    agree with them first; the message says who registered it).
Concurrent additions by two people are safe: each stream is its own set of
keys, and the label list is re-merged and verified after the write.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import load_catalog_config, metacat_client, utcnow  # noqa: E402
from registry import (  # noqa: E402
    LABEL_OK, check_owner, check_schema, merged_labels, new_revision, streams_from_metadata,
)
from sync_registry import sync_one  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ensemble", required=True, help="registered ensemble name (see retrieve_records.py --summary)")
    ap.add_argument("--stream", required=True, help="new stream label, e.g. b, 3000, djm-5")
    ap.add_argument("--dir", help="storage directory under the archive root (default <ensemble>-<stream>)")
    ap.add_argument("--parent-stream", help="stream this one was branched from")
    ap.add_argument("--parent-trajectory", type=int, help="trajectory of the parent stream it was branched at")
    ap.add_argument("--note", help="free-text note stored with the stream")
    ap.add_argument("--force", action="store_true", help="add to an ensemble registered by someone else")
    ap.add_argument("--dry-run", action="store_true", help="run every check, write nothing")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)
    ns = cfg["metacat"]["namespace"]
    client = metacat_client(cfg, need_auth=True)
    user = client.auth_info()[0]
    name, label = args.ensemble, args.stream
    did = f"{ns}:{name}"

    ds = client.get_dataset(did)
    if ds is None:
        sys.exit(f"no ensemble {name!r} in the catalog. Registered ensembles: "
                 "`python3 scripts/retrieve_records.py --summary`; a new one is registered with register_ensemble.py.")
    md = ds.get("metadata") or {}
    check_schema(md, did)
    if "lqcd.catalog.source" not in md:
        sys.exit(f"{did} predates the catalog registry (schema 2); run scripts/migrations/registry_v2.py first.")
    if md.get("lqcd.catalog.inherits_streams"):
        sys.exit(f"{did} is a derived dataset that takes its streams from {md.get('lqcd.parent.ensemble')}; "
                 "add the stream to that ensemble.")
    sync_one(client, ns, name, quiet=True)          # start from a current local snapshot

    streams = streams_from_metadata(md)
    labels = list(md.get("lqcd.stream_labels") or [])
    if not LABEL_OK.match(label):
        sys.exit(f"stream label {label!r}: use letters, digits, '-' and '_' only.")
    if label in streams or label in labels:
        st = streams.get(label, {})
        who = f" (registered by {st['registered_by']} on {st.get('registered_at', '?')[:10]})" if st.get("registered_by") else ""
        sys.exit(f"stream {label!r} already exists in {name}{who}. Stream labels are permanent; "
                 f"pick a different label. Existing: {', '.join(labels or streams)}")
    sdir = args.dir or f"{name}-{label}"
    clash = [l for l, st in streams.items() if st.get("dir", "").rstrip("/") == sdir.rstrip("/")]
    if clash:
        sys.exit(f"directory {sdir!r} is already registered for stream {clash[0]!r} of {name}; "
                 "pass the new stream's own directory with --dir.")
    if args.parent_stream and args.parent_stream not in streams and args.parent_stream not in labels:
        sys.exit(f"--parent-stream {args.parent_stream!r} is not a stream of {name}: {', '.join(labels or streams)}")
    check_owner(ds, user, args.force, did)

    now = utcnow()
    new = {f"lqcd.streams.{label}.dir": sdir}
    if args.parent_stream:
        new[f"lqcd.streams.{label}.parent_stream"] = args.parent_stream
    if args.parent_trajectory is not None:
        new[f"lqcd.streams.{label}.parent_trajectory"] = args.parent_trajectory
    if args.note:
        new[f"lqcd.streams.{label}.note"] = args.note
    new[f"lqcd.streams.{label}.registered_by"] = user
    new[f"lqcd.streams.{label}.registered_at"] = now
    if args.dry_run:
        print(f"[dry-run] all checks passed; would add to {did}:")
        for k, v in new.items():
            print(f"  {k} = {v!r}")
        return 0

    # Write, then verify; another add_stream running at the same moment may
    # have rewritten the label list, so re-merge until ours is in it.
    for attempt in range(1, 4):
        cur = (client.get_dataset(did) or {}).get("metadata") or {}
        upd = dict(new)
        upd["lqcd.stream_labels"] = merged_labels(cur.get("lqcd.stream_labels"), [label])
        # lqcd.n_streams counts streams with catalogued files; the declare after this updates it
        upd["lqcd.catalog.revision"] = new_revision(user)
        client.update_dataset(did, metadata=upd, mode="update")
        time.sleep(1)
        after = (client.get_dataset(did) or {}).get("metadata") or {}
        mine = after.get(f"lqcd.streams.{label}.registered_at") == now
        listed = label in (after.get("lqcd.stream_labels") or [])
        if mine and listed:
            break
        if not mine:
            sys.exit(f"stream {label!r} was registered by {after.get(f'lqcd.streams.{label}.registered_by')!r} "
                     "at the same moment; pick a different label.")
    else:
        sys.exit(f"could not confirm {label!r} in {did}'s stream list after 3 attempts; re-run to repair.")

    sync_one(client, ns, name, quiet=True)
    print(f"registered stream {label!r} of {name} (directory {sdir}); snapshot ensembles/{name}.toml updated.")
    print("Next, catalog its files:")
    print(f"  python3 scripts/hpss_inventory.py  ensembles/{name}.toml        # or write work/{name}/inventory.json")
    print(f"  python3 scripts/build_records.py   ensembles/{name}.toml")
    print(f"  python3 scripts/declare_records.py work/{name}/records.json")
    print(f"  python3 scripts/retrieve_records.py {name} --diff work/{name}/records.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
