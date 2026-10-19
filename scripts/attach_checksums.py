#!/usr/bin/env python3
"""
Attach checksums to catalog records after the fact (optional step).

    # files that live on FDP dCache: ask dCache for the hash it computed on arrival
    python3 scripts/attach_checksums.py cl3_32_64_b6p5_m0p1788 --from-dcache            # report only
    python3 scripts/attach_checksums.py cl3_32_64_b6p5_m0p1788 --from-dcache --apply    # write to MetaCat

    # files hashed somewhere else (e.g. FDP's generate_metadata.py, or sha256sum on a DTN)
    python3 scripts/attach_checksums.py cl3_32_64_b6p5_m0p1788 --from-json hashes.json --apply

Records are declared without checksums when the bytes are only on tape (HPSS
keeps none). Once a copy exists on the Fermi Data Platform dCache, the
cheapest hash is the one dCache already has: this script does what FDP's
examples/generate_metadata_remote.py does for the checksum part, a GET on the
dCache REST API

    https://amsc.fnal.gov:3880/api/v1/namespace/<path>?checksum=true

with a bearer token found the same way (BEARER_TOKEN, BEARER_TOKEN_FILE,
$XDG_RUNTIME_DIR/bt_u$UID, /tmp/bt_u$UID; get one with
`htgettoken -i amsc -a htvaultprod.fnal.gov`). The dCache path comes from the
record's own locations: an https:// URL on a storage entry's https_base, or a
globus:// URL on an FDP collection listed in catalog.toml.

--from-json takes {"<catalog file name>": {"sha256": "<hex>", ...}} or
{"<name>": "<hex>"} with --algorithm, for hashes computed anywhere else.

Without --apply the script only writes work/<ensemble>/checksums.json and a
summary. With --apply it needs a MetaCat token and calls update_file() per
record (merge, never replace), also stamping lqcd.checksum.source.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import dataset_did, load_catalog_config, metacat_client, read_json, utcnow, work_dir, write_json  # noqa: E402


def bearer_token() -> str | None:
    """Token discovery as in the FDP example scripts / SciTokens convention."""
    if os.environ.get("BEARER_TOKEN"):
        return os.environ["BEARER_TOKEN"].strip()
    candidates = [os.environ.get("BEARER_TOKEN_FILE"),
                  os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), f"bt_u{os.getuid()}"),
                  f"/tmp/bt_u{os.getuid()}"]
    for p in candidates:
        if p and os.path.exists(p):
            return Path(p).read_text().strip()
    return None


def dcache_path(meta: dict, cfg: dict) -> tuple[str, str] | None:
    """(api_base, path) for a record whose bytes are on an FDP dCache, else None."""
    urls = [meta.get("AmSC.common.location", "")]
    locs = meta.get("fn.locations") or []
    urls += [locs] if isinstance(locs, str) else list(locs)
    for key, st in cfg["storage"].items():
        api = st.get("dcache_api")
        if not api:
            continue
        for u in urls:
            if not u:
                continue
            p = urllib.parse.urlparse(u)
            if p.scheme == "globus" and p.netloc == st.get("globus_collection"):
                return api, p.path
            if p.scheme == "https" and st.get("https_base") and u.startswith(st["https_base"]):
                return api, p.path
    return None


def dcache_checksums(api: str, path: str, token: str | None, insecure: bool) -> dict:
    url = f"{api.rstrip('/')}{path}?checksum=true"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    ctx = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        data = json.load(r)
    return {c["type"].lower(): c["value"] for c in data.get("checksums", []) if "type" in c and "value" in c}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ensemble", help="ensemble name (dataset = <namespace>:<ensemble>)")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-dcache", action="store_true", help="query the FDP dCache REST API for stored checksums")
    src.add_argument("--from-json", metavar="FILE", help="{name: {alg: hex}} or {name: hex} (with --algorithm)")
    ap.add_argument("--algorithm", default="sha256", help="algorithm for bare-string --from-json values")
    ap.add_argument("--kind", help="only records with this lqcd.artifact.kind")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--insecure", action="store_true", help="skip TLS verification on the dCache API (FDP's example does)")
    ap.add_argument("--apply", action="store_true", help="write checksums to MetaCat (needs a token)")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)
    ns = cfg["metacat"]["namespace"]
    did = dataset_did(cfg, args.ensemble)
    client = metacat_client(cfg, need_auth=args.apply)

    mql = f"files from {did}" + (f' where lqcd.artifact.kind = "{args.kind}"' if args.kind else "")
    records = list(client.query(mql, namespace=ns, with_metadata=True))
    if args.limit:
        records = records[: args.limit]
    sys.stderr.write(f"[mql] {mql}: {len(records)} records\n")

    found, skipped, errors = {}, [], []
    if args.from_json:
        given = read_json(args.from_json)
        for r in records:
            v = given.get(r["name"])
            if v is None:
                skipped.append(r["name"])
            else:
                found[r["name"]] = {args.algorithm: v} if isinstance(v, str) else dict(v)
        source = f"json:{Path(args.from_json).name}"
    else:
        token = bearer_token()
        if not token:
            sys.stderr.write("[warn] no bearer token found (BEARER_TOKEN / bt_u$UID); dCache may answer 401. "
                             "Get one with: htgettoken -i amsc -a htvaultprod.fnal.gov\n")
        for r in records:
            loc = dcache_path(r.get("metadata") or {}, cfg)
            if loc is None:
                skipped.append(r["name"])
                continue
            try:
                cs = dcache_checksums(loc[0], loc[1], token, args.insecure)
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
                errors.append(f"{r['name']}: {e}")
                continue
            if cs:
                found[r["name"]] = cs
            else:
                skipped.append(r["name"])
        source = "dcache"

    wd = work_dir(cfg, args.ensemble)
    report = {"checked_at": utcnow(), "dataset": did, "source": source, "n_records": len(records),
              "n_with_checksums": len(found), "n_skipped": len(skipped), "errors": errors, "checksums": found}
    write_json(wd / "checksums.json", report)
    print(f"{len(found)} records with checksums, {len(skipped)} without a usable location/hash, {len(errors)} errors; "
          f"wrote {wd / 'checksums.json'}")
    for e in errors[:5]:
        print("  error:", e)

    if not args.apply:
        if found:
            print("add --apply to write them to MetaCat")
        return 1 if errors else 0

    n = 0
    for name, cs in found.items():
        client.update_file(did=f"{ns}:{name}", checksums=cs, replace=False,
                           metadata={"lqcd.checksum.source": source, "lqcd.checksum.updated_at": utcnow()})
        n += 1
        if n % 100 == 0:
            print(f"  updated {n}/{len(found)}")
    print(f"wrote checksums onto {n} records in {did}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
