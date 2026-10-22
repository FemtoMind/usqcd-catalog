#!/usr/bin/env python3
"""
Step 3: push records.json into MetaCat (the only step that writes to the catalog).

    declare_records.py work/cl3_32_64_b6p5_m0p1788/records.json --dry-run   # server-side checks only
    declare_records.py work/cl3_32_64_b6p5_m0p1788/records.json             # declare new files
    declare_records.py work/.../records.json --update-existing              # also refresh metadata
                                                                            # of files already declared

Behaviour, in order:
  1. Needs a valid MetaCat token (reads are anonymous, writes are not). If the
     token is missing/expired the script stops and prints the auth command.
  2. Creates the ensemble dataset if it does not exist; with --update-existing
     merges the dataset metadata otherwise.
  3. Splits the file records into "new" (not in the namespace) and "existing"
     (already declared). New files are declared in batches with declare_files();
     --dry-run makes the server validate them without declaring. When new
     files land in an existing dataset (a stream was added), the dataset
     metadata (stream list, counts) is merged as well.
  4. Existing files are left alone unless --update-existing, in which case
     their metadata is merged (update_file, replace=False), so FNAL-stamped
     fields (fn.locations, AmSC.common.fqn, ...) survive.

Guards (the catalog is the ensemble registry, shared by every group):
  * refuses a record whose registry schema is newer than these scripts;
  * refuses to touch an ensemble, or a stream, registered by another user
    unless --force (and says who registered it);
  * refuses if the catalog's registry entry changed since your snapshot was
    synced (someone else added a stream, say): run sync_registry.py, rebuild,
    re-run. Stream labels are merged with what is in the catalog, never shrunk.
After a write the local snapshot is regenerated from the catalog.

Idempotent: re-running after a partial failure declares only what is missing.
Writes work/<ensemble>/declare_<timestamp>.json with what was done.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import (  # noqa: E402
    human_bytes, load_catalog_config, metacat_client, read_json, utcnow, write_json,
)
from registry import (  # noqa: E402
    check_owner, check_revision, check_schema, check_stream_owner, merged_labels, new_revision,
)
from sync_registry import sync_one  # noqa: E402


def existing_names(client, namespace: str, names: list[str]) -> set[str]:
    """Names (from `names`) that already exist in the namespace. Uses one MQL
    query for the whole namespace rather than N get_file round trips."""
    # Every record this pipeline writes carries lqcd.artifact.kind, so the
    # `present` clause narrows the server-side scan to those; the name filter
    # below is what actually decides.
    found = set()
    wanted = set(names)
    q = f"files where namespace = \"{namespace}\" and lqcd.artifact.kind present"
    try:
        for r in client.query(q, with_metadata=False):
            if r["name"] in wanted:
                found.add(r["name"])
    except Exception as e:  # fall back to per-file lookups if the MQL form is rejected
        sys.stderr.write(f"[warn] bulk query failed ({e}); falling back to get_file per record\n")
        for n in names:
            try:
                if client.get_file(name=n, namespace=namespace, with_metadata=False, with_provenance=False):
                    found.add(n)
            except Exception:
                pass
    return found


REQUIRED_FIELDS = ("AmSC.common.type", "AmSC.common.description", "AmSC.common.location",
                   "lqcd.ensemble", "lqcd.artifact.kind")


def local_checks(files: list[dict]) -> list[str]:
    """Problems that would make the server reject records.json; empty when clean."""
    errors = []
    names = [f["name"] for f in files]
    if len(set(names)) != len(names):
        errors.append("duplicate names in records.json")
    for f in files:
        md = f.get("metadata", {})
        for k in REQUIRED_FIELDS:
            if k not in md:
                errors.append(f"{f['name']}: missing {k}")
                break
        if not isinstance(f.get("size"), int) or f["size"] < 0:
            errors.append(f"{f['name']}: bad size {f.get('size')!r}")
    return errors


REGISTRY_KEYS = ("lqcd.catalog.source", "lqcd.catalog.inherits_streams", "lqcd.stream_labels")


def registry_part(md: dict) -> dict:
    return {k: v for k, v in md.items() if k in REGISTRY_KEYS or k.startswith("lqcd.streams.")}


def prepare_dataset_metadata(local: dict, remote: dict | None, user: str) -> tuple[dict, bool]:
    """The dataset metadata to write: the local record, with stream labels
    merged (never shrunk), registered_by/at stamped on streams that lack them,
    and a new registry revision only when the registry part changes.
    Returns (metadata, registry_changed)."""
    remote = remote or {}
    md = dict(local)
    md["lqcd.stream_labels"] = merged_labels(remote.get("lqcd.stream_labels"), local.get("lqcd.stream_labels", []))
    # lqcd.n_streams stays as built: streams with catalogued files, not registered labels
    now = utcnow()
    labels = {k.split(".")[2] for k in md if k.startswith("lqcd.streams.") and k.endswith(".dir")}
    for label in labels:
        for field, value in (("registered_by", user), ("registered_at", now)):
            key = f"lqcd.streams.{label}.{field}"
            if key not in md:
                md[key] = remote.get(key, value)
    changed = registry_part(md) != registry_part(remote)
    md["lqcd.catalog.revision"] = new_revision(user) if changed else remote.get("lqcd.catalog.revision", new_revision(user))
    return md, changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("records_json")
    ap.add_argument("--force", action="store_true",
                    help="write even if the ensemble or a stream was registered by someone else, or if the catalog "
                         "changed since your local copy was synced")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    ap.add_argument("--dry-run", action="store_true", help="validate on the server, declare nothing")
    ap.add_argument("--update-existing", action="store_true", help="merge metadata into already-declared files and the dataset")
    ap.add_argument("--dataset-only", action="store_true",
                    help="with --update-existing: refresh only the dataset record (e.g. new observables), touch no files")
    ap.add_argument("--batch-size", type=int, default=200)
    ap.add_argument("--limit", type=int, help="declare at most N new files (for a first smoke test)")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)
    rec = read_json(args.records_json)
    ns = cfg["metacat"]["namespace"]
    ds_did = rec["dataset"]["did"]
    files = rec["files"]
    client = metacat_client(cfg, need_auth=True)
    user = client.auth_info()[0]
    name = ds_did.split(":", 1)[1]

    report = {"started_at": utcnow(), "records_json": str(Path(args.records_json).resolve()),
              "dataset": ds_did, "dry_run": args.dry_run, "update_existing": args.update_existing}

    # ---- guards: schema version, ownership, stale local copy
    ds = client.get_dataset(ds_did)
    remote_md = (ds or {}).get("metadata") or {}
    if ds is not None:
        check_schema(remote_md, ds_did)
        check_owner(ds, user, args.force, ds_did)
        check_revision(remote_md, rec.get("registry_revision"), args.force, ds_did, name)
        touched = sorted({f["metadata"].get("lqcd.stream") for f in files if f["metadata"].get("lqcd.stream")})
        check_stream_owner(remote_md, touched, user, args.force, ds_did)
    ds_md, registry_changed = prepare_dataset_metadata(rec["dataset"]["metadata"], remote_md, user)
    wrote_dataset = False

    # ---- dataset
    if ds is None:
        if args.dry_run:
            print(f"[dry-run] would create dataset {ds_did}")
        else:
            client.create_dataset(ds_did, metadata=ds_md, description=ds_md.get("AmSC.common.display_name", ""))
            print(f"created dataset {ds_did}")
            wrote_dataset = True
        report["dataset_action"] = "create"
    elif args.update_existing and not args.dry_run:
        client.update_dataset(ds_did, metadata=ds_md, mode="update")
        print(f"updated dataset metadata {ds_did}" + (" (registry changed: new revision)" if registry_changed else ""))
        wrote_dataset = True
        report["dataset_action"] = "update"
    else:
        print(f"dataset {ds_did} exists ({ds.get('file_count', '?')} files)")
        report["dataset_action"] = "none"

    if args.dataset_only:
        report.update({"finished_at": utcnow(), "n_new": 0, "n_existing": 0, "n_declared": 0,
                       "n_validated": 0, "n_updated": 0, "errors": []})
        write_json(Path(args.records_json).parent / f"declare_{time.strftime('%Y%m%dT%H%M%S')}.json", report)
        print("dataset-only: file records not touched")
        if wrote_dataset:
            after_write(client, ns, name, args.records_json, rec, ds_md)
        return 0

    # ---- split new / existing
    have = existing_names(client, ns, [f["name"] for f in files])
    new = [f for f in files if f["name"] not in have]
    old = [f for f in files if f["name"] in have]
    if args.limit:
        new = new[: args.limit]
    print(f"{len(files)} records: {len(new)} new, {len(old)} already declared "
          f"({human_bytes(sum(f['size'] for f in new))} new)")

    # ---- local sanity checks (always), then server-side declare/validate
    errors = local_checks(files)
    if errors:
        for e in errors[:10]:
            print(f"  ERROR {e}", file=sys.stderr)
        sys.exit(1)
    print(f"local checks passed: {len(files)} records, {len({f['name'] for f in files})} unique names, "
          "required fields present")

    declared = []
    if args.dry_run and ds is None:
        print("[dry-run] the dataset does not exist yet, so the server cannot validate file records "
              "against it; run without --dry-run (optionally with --limit N) to create it.")
        new = []
    for i in range(0, len(new), args.batch_size):
        batch = new[i:i + args.batch_size]
        try:
            client.declare_files(ds_did, files=batch, namespace=ns, dry_run=args.dry_run)
            declared += [f["name"] for f in batch]
            print(f"  {'[dry-run] validated' if args.dry_run else 'declared'} {i + len(batch)}/{len(new)}")
        except Exception as e:
            msg = f"batch {i // args.batch_size}: {type(e).__name__}: {e}"
            errors.append(msg)
            print(f"  ERROR {msg}", file=sys.stderr)
            break  # stop on first failure; re-run picks up where we left off

    # New files in an existing dataset (a stream added, a directory that grew)
    # change the dataset's stream list and counts, so merge the dataset
    # metadata too; without this, only --update-existing would refresh it.
    if declared and ds is not None and not args.dry_run and not args.update_existing:
        try:
            client.update_dataset(ds_did, metadata=ds_md, mode="update")
            print(f"refreshed dataset metadata {ds_did} (streams, counts)")
            report["dataset_action"] = "refresh"
            wrote_dataset = True
        except Exception as e:
            errors.append(f"dataset refresh: {type(e).__name__}: {e}")
            print(f"  ERROR dataset refresh: {e}", file=sys.stderr)

    # ---- refresh existing
    updated = []
    if args.update_existing and old and not args.dry_run:
        for j, f in enumerate(old, 1):
            try:
                client.update_file(did=f"{ns}:{f['name']}", size=f["size"], metadata=f["metadata"], replace=False)
                updated.append(f["name"])
            except Exception as e:
                errors.append(f"update {f['name']}: {type(e).__name__}: {e}")
                print(f"  ERROR update {f['name']}: {e}", file=sys.stderr)
            if j % 100 == 0:
                print(f"  updated {j}/{len(old)}")
        # files declared earlier into the namespace but not yet in this dataset
        try:
            client.add_files(ds_did, file_list=[{"did": f"{ns}:{f['name']}"} for f in old], namespace=ns)
        except Exception as e:
            sys.stderr.write(f"[warn] add_files to {ds_did}: {e}\n")
        print(f"updated metadata on {len(updated)} existing files")

    report.update({
        "finished_at": utcnow(), "n_new": len(new), "n_existing": len(old),
        "n_declared": 0 if args.dry_run else len(declared), "n_validated": len(declared) if args.dry_run else 0,
        "n_updated": len(updated), "errors": errors,
    })
    outp = Path(args.records_json).parent / f"declare_{time.strftime('%Y%m%dT%H%M%S')}.json"
    write_json(outp, report)
    print(json.dumps({k: v for k, v in report.items() if k not in ("records_json",)}, indent=1))
    if wrote_dataset:
        after_write(client, ns, name, args.records_json, rec, ds_md)
    return 1 if errors else 0


def after_write(client, ns: str, name: str, records_json: str, rec: dict, ds_md: dict) -> None:
    """Keep the local side current after a write: records.json now matches the
    revision we wrote (so the next declare is not refused as stale), and the
    snapshot is regenerated from the catalog."""
    rec["registry_revision"] = ds_md.get("lqcd.catalog.revision")
    write_json(Path(records_json), rec)
    try:
        state = sync_one(client, ns, name, quiet=True)
        if state == "written":
            print(f"synced the local snapshot of {name} from the catalog (commit it when convenient)")
    except Exception as e:
        sys.stderr.write(f"[warn] could not sync the local snapshot: {e}\n")


if __name__ == "__main__":
    sys.exit(main())
