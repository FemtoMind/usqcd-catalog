#!/usr/bin/env python3
"""
Step 4: pull an ensemble's records back out of MetaCat, and check the round trip.

    retrieve_records.py cl3_32_64_b6p5_m0p1788                       # dump everything
    retrieve_records.py cl3_32_64_b6p5_m0p1788 --stream a --kind gauge_config \
        --trajectory-min 1000 --trajectory-max 1200
    retrieve_records.py cl3_32_64_b6p5_m0p1788 --diff work/cl3_32_64_b6p5_m0p1788/records.json
    retrieve_records.py cl3_32_64_b6p5_m0p1788 --kind gauge_config --paths   # one HPSS path per line
    retrieve_records.py --list-ensembles                                      # what ensembles exist
    retrieve_records.py --kind gauge_config --beta 6.5 --kappa 0.130849       # discovery across ensembles;
                                                                              # --mass or --kappa both match
                                                                              # records stored either way

Reads are anonymous; no token needed. Writes work/<ensemble>/catalog_dump.json
in the same shape as records.json so the two can be diffed field by field.

--diff compares every record we intended to write (records.json) against what
the catalog returns: missing files, extra files, size mismatches, and any
AmSC.common.* / lqcd.* value that differs. Fields the catalog server adds on
its own (fn.*, AmSC.common.fqn, tags, version, ...) are ignored. Exit status
is 0 when the catalog matches, 1 otherwise, so this can gate a pipeline.

--paths prints the storage path of each hit (from lqcd.storage.path), which is
what you feed to `hsi get` / a Globus batch file to actually move the data.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog_common import (  # noqa: E402
    dataset_did, human_bytes, kappa_from_mass, load_catalog_config, mass_from_kappa, metacat_client,
    read_json, utcnow, work_dir, write_json,
)

IGNORED_PREFIXES = ("fn.", "AmSC.common.fqn", "AmSC.common.tags", "AmSC.common.version",
                    "lqcd.catalog.updated_at")


def mql_quote(v):
    return str(v) if isinstance(v, (int, float)) else '"' + str(v).replace('"', '\\"') + '"'


def mass_kappa_clause(flavor: str, mass: float | None, kappa: float | None, tol: float) -> str:
    """Match records that stored either the bare mass or kappa for this flavor.

    Both representations are searched, so a record written by a group that
    stored only kappa is found by a user asking for a mass, and vice versa.
    `tol` is an absolute tolerance on the mass; it is propagated to kappa
    through dkappa/dm = -2 kappa^2 (isotropic Wilson-type relation).
    """
    if mass is None:
        mass = mass_from_kappa(kappa)
    if kappa is None:
        kappa = kappa_from_mass(mass)
    ktol = max(2.0 * kappa * kappa * tol, 1e-12)
    mk, kk = f"lqcd.mass.{flavor}", f"lqcd.kappa.{flavor}"
    return (f"(({mk} >= {mass - tol:.12g} and {mk} <= {mass + tol:.12g}) or "
            f"({kk} >= {kappa - ktol:.12g} and {kk} <= {kappa + ktol:.12g}))")


def build_mql(did: str | None, args) -> str:
    where = []
    if args.kind:
        where.append(f"lqcd.artifact.kind = {mql_quote(args.kind)}")
    if args.stream:
        where.append(f"lqcd.stream = {mql_quote(args.stream)}")
    if args.trajectory is not None:
        where.append(f"lqcd.trajectory = {args.trajectory}")
    if args.trajectory_min is not None:
        where.append(f"lqcd.trajectory >= {args.trajectory_min}")
    if args.trajectory_max is not None:
        where.append(f"lqcd.trajectory <= {args.trajectory_max}")
    if args.beta is not None:
        where.append(f"lqcd.beta = {args.beta}")
    if args.mass is not None or args.kappa is not None:
        where.append(mass_kappa_clause(args.flavor, args.mass, args.kappa, args.tol))
    if did:
        q = f"files from {did}"
    else:
        q = "files"
        where.insert(0, "lqcd.ensemble present")
    if where:
        q += " where " + " and ".join(where)
    return q


def list_ensembles(client, ns: str) -> list[dict]:
    """All datasets in the namespace that look like ensembles, with the physics
    fields people search on. Reads only."""
    out = []
    for d in client.list_datasets(namespace_pattern=ns):
        did = f"{d['namespace']}:{d['name']}"
        full = client.get_dataset(did) or {}
        md = full.get("metadata") or d.get("metadata") or {}
        # Keep datasets this pipeline wrote (kind = gauge_ensemble) and ones
        # with no kind at all that still name an ensemble; skip other kinds.
        kind = md.get("lqcd.artifact.kind")
        if kind != "gauge_ensemble" and not (kind is None and "lqcd.ensemble" in md):
            continue
        out.append({
            "did": did, "file_count": full.get("file_count"),
            "ensemble": md.get("lqcd.ensemble"), "nflavors": md.get("lqcd.nflavors"),
            "action": md.get("lqcd.action.fermion"), "beta": md.get("lqcd.beta"),
            "volume": md.get("lqcd.volume"), "mass_light": md.get("lqcd.mass.light"),
            "kappa_light": md.get("lqcd.kappa.light"), "streams": md.get("lqcd.streams"),
            "n_configs": md.get("lqcd.n_configs"), "display_name": md.get("AmSC.common.display_name"),
        })
    return out


def compare(expected: dict, got: dict) -> dict:
    """Diff records.json (expected) against a catalog dump (got), ignoring
    server-stamped fields; numbers are compared with a 1e-9 tolerance."""
    exp_files = {f["name"]: f for f in expected["files"]}
    got_files = {f["name"]: f for f in got["files"]}
    missing = sorted(set(exp_files) - set(got_files))
    extra = sorted(set(got_files) - set(exp_files))
    mismatched = {}
    for n in sorted(set(exp_files) & set(got_files)):
        diffs = {}
        e, g = exp_files[n], got_files[n]
        if e["size"] != g["size"]:
            diffs["size"] = [e["size"], g["size"]]
        for k, v in e["metadata"].items():
            if k.startswith(IGNORED_PREFIXES):
                continue
            gv = g["metadata"].get(k, "<absent>")
            if gv != v and not (isinstance(v, (int, float)) and isinstance(gv, (int, float)) and abs(gv - v) < 1e-9):
                diffs[k] = [v, gv]
        if diffs:
            mismatched[n] = diffs
    ds_diffs = {}
    for k, v in expected["dataset"]["metadata"].items():
        if k.startswith(IGNORED_PREFIXES):
            continue
        gv = got["dataset"]["metadata"].get(k, "<absent>")
        if gv != v:
            ds_diffs[k] = [v, gv]
    return {"missing": missing, "extra": extra, "mismatched": mismatched, "dataset": ds_diffs,
            "ok": not (missing or extra or mismatched or ds_diffs)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ensemble", nargs="?", help="ensemble name (dataset = <namespace>:<ensemble>); "
                    "omit to search every ensemble in the namespace by physics filters")
    ap.add_argument("--config", help="site config (default: catalog.toml in repo root)")
    ap.add_argument("--list-ensembles", action="store_true", help="print the ensembles in the catalog and exit")
    ap.add_argument("--kind", help="lqcd.artifact.kind filter (gauge_config, hmc_input, hmc_log_archive)")
    ap.add_argument("--stream", help="lqcd.stream filter (main, a, b, ...)")
    ap.add_argument("--trajectory", type=int)
    ap.add_argument("--trajectory-min", type=int)
    ap.add_argument("--trajectory-max", type=int)
    ap.add_argument("--beta", type=float)
    ap.add_argument("--mass", type=float, help="bare quark mass (lattice units); also matches records that stored kappa")
    ap.add_argument("--kappa", type=float, help="hopping parameter; also matches records that stored the bare mass")
    ap.add_argument("--flavor", default="light", help="which lqcd.mass.<flavor>/lqcd.kappa.<flavor> to match (default light)")
    ap.add_argument("--tol", type=float, default=1e-4, help="absolute tolerance on the bare mass for --mass/--kappa "
                    "(default 1e-4: a kappa typed to 6 digits is ~1.5e-5 in mass; ensembles differ by >1e-3)")
    ap.add_argument("--mql", help="raw MQL to run instead of the generated query")
    ap.add_argument("-o", "--output", help="dump JSON (default: work/<ensemble>/catalog_dump.json)")
    ap.add_argument("--diff", metavar="RECORDS_JSON", help="compare the catalog against this records.json")
    ap.add_argument("--paths", action="store_true", help="print one storage path per hit to stdout")
    args = ap.parse_args()

    cfg = load_catalog_config(args.config)
    ns = cfg["metacat"]["namespace"]
    client = metacat_client(cfg, need_auth=False)

    if args.list_ensembles:
        rows = list_ensembles(client, ns)
        print(f"{'dataset':40} {'Nf':>2} {'action':8} {'beta':>6} {'volume':>16} {'m_light':>10} {'kappa_l':>10} {'cfgs':>5}  streams")
        for r in rows:
            print(f"{r['did']:40} {str(r['nflavors'] or ''):>2} {str(r['action'] or ''):8} {str(r['beta'] or ''):>6} "
                  f"{str(r['volume'] or ''):>16} {str(r['mass_light'] if r['mass_light'] is not None else ''):>10} "
                  f"{str(r['kappa_light'] if r['kappa_light'] is not None else ''):>10} {str(r['n_configs'] or ''):>5}  {r['streams'] or ''}")
        write_json(work_dir(cfg, "_namespace") / "ensembles.json", rows)
        return 0

    did = None
    ds = {}
    if args.ensemble:
        did = dataset_did(cfg, args.ensemble)
        ds = client.get_dataset(did)
        if ds is None:
            sys.exit(f"dataset {did} not found in {cfg['metacat']['server_url']}")
    elif not (args.mql or args.beta is not None or args.mass is not None or args.kappa is not None or args.kind):
        sys.exit("give an ensemble name, or at least one of --beta/--mass/--kappa/--kind to search across ensembles")
    mql = args.mql or build_mql(did, args)
    sys.stderr.write(f"[mql] {mql}\n")
    hits = list(client.query(mql, namespace=ns, with_metadata=True))
    hits.sort(key=lambda r: (r["metadata"].get("lqcd.stream", ""), r["metadata"].get("lqcd.artifact.kind", ""),
                             r["metadata"].get("lqcd.trajectory", -1), r["name"]))

    files = [{"namespace": r["namespace"], "name": r["name"], "fid": r.get("fid"), "size": r.get("size"),
              "checksums": r.get("checksums") or {}, "metadata": r.get("metadata") or {}} for r in hits]
    dump = {
        "retrieved_at": utcnow(), "server": cfg["metacat"]["server_url"], "mql": mql,
        "dataset": {"did": did, "metadata": ds.get("metadata") or {}, "file_count": ds.get("file_count")},
        "summary": {
            "n_files": len(files), "total_bytes": sum(f["size"] or 0 for f in files),
            "kinds": dict(Counter(f["metadata"].get("lqcd.artifact.kind") for f in files)),
            "streams": dict(Counter(f["metadata"].get("lqcd.stream") for f in files)),
        },
        "files": files,
    }
    outp = Path(args.output) if args.output else work_dir(cfg, args.ensemble or "_namespace") / "catalog_dump.json"
    write_json(outp, dump)

    s = dump["summary"]
    s["ensembles"] = dict(Counter(f["metadata"].get("lqcd.ensemble") for f in files))
    sys.stderr.write(f"{did or ns}: {s['n_files']} hits, {human_bytes(s['total_bytes'])}, kinds={s['kinds']}, "
                     f"ensembles={s['ensembles']}, streams={s['streams']}\n")
    sys.stderr.write(f"wrote {outp}\n")

    if args.paths:
        for f in files:
            print(f["metadata"].get("lqcd.storage.path", ""))

    if args.diff:
        expected = read_json(args.diff)
        result = compare(expected, dump)
        write_json(outp.parent / "catalog_diff.json", result)
        print(f"round-trip check against {args.diff}:")
        print(f"  missing from catalog: {len(result['missing'])}")
        print(f"  extra in catalog:     {len(result['extra'])}")
        print(f"  mismatched files:     {len(result['mismatched'])}")
        print(f"  dataset field diffs:  {len(result['dataset'])}")
        for n, d in list(result["mismatched"].items())[:5]:
            print(f"    {n}: {d}")
        for k, d in list(result["dataset"].items())[:10]:
            print(f"    dataset {k}: expected {d[0]!r} got {d[1]!r}")
        print("  OK" if result["ok"] else "  DIFFERENCES FOUND (see catalog_diff.json)")
        return 0 if result["ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
