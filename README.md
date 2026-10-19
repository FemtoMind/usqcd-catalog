# usqcd-catalog

Scripts and conventions for cataloging USQCD lattice-QCD data (gauge-field
ensembles today; propagators and correlators next) in the AmSC Fermi Data
Platform MetaCat catalog, starting from files that live on a tape archive
(NERSC HPSS). Built around the NPLQCD `clover-iso` ensembles; meant to be
copied and adapted by other USQCD groups.

The pipeline is four small Python scripts with JSON between them, so every
step can be inspected and re-run on its own:

```
ensembles/<name>.toml  (hand-written: physics, streams, file rules)
        │
        ▼
1. scripts/hpss_inventory.py   hsi ls -lR  ──►  work/<name>/inventory.json
2. scripts/build_records.py    TOML + inventory ──►  work/<name>/records.json
3. scripts/declare_records.py  records.json ──► MetaCat   (the only write step)
4. scripts/retrieve_records.py MetaCat ──► work/<name>/catalog_dump.json, --diff records.json
```

Site-specific values (catalog URL, namespace, storage Globus UUID, HPSS root,
Python interpreter) live in `catalog.toml`. Ensemble-specific values live in
`ensembles/<name>.toml`. Nothing in `scripts/` hard-codes either.

## Data model

* **Ensemble = MetaCat dataset** `lqcd:<ensemble>` (e.g. `lqcd:cl3_32_64_b6p5_m0p1788`).
  An ensemble is a set of Markov chain *streams* generated with identical
  parameters. Streams are **not** separate datasets.
* **Every file carries `lqcd.stream` and `lqcd.trajectory`.** Trajectory
  numbering restarts at 0 in every stream, so (ensemble, stream, trajectory)
  is the unique physics key. `lqcd.stream_dir` records the storage directory.
* **One file record per stored file**, classified by `lqcd.artifact.kind`:
  `gauge_config` (LIME), `hmc_input` (Chroma restart XML), `hmc_log_archive`
  (log tarballs). Rules are regexes in the ensemble TOML, so a different
  group's layout is a different set of rules, not different code.
* **Ensemble-level physics is on every file** (`lqcd.beta`, `lqcd.mass.light`,
  `lqcd.action.*`, `lqcd.hmc.*`, ...). A query hit is self-describing; nobody
  has to join against the dataset record.
* **`AmSC.common.location` is a `globus://<collection-uuid>/<path>` URL** for
  the storage system the bytes are actually on (NERSC HPSS here). Anyone with
  Globus access to that collection can transfer from it without knowing `hsi`.
  `lqcd.storage.*` carries the same facts as plain queryable fields.
* **Checksums are absent for now.** HPSS stores none and the bytes are on
  tape only. A later pass can stage, hash, and `update_file(checksums=...)`.

Full field list: `docs/schema.md`.

## Setup

Steps 1 and 2 (inventory, build) use only the Python standard library
(>= 3.11, for `tomllib`). Steps 3 and 4 talk to MetaCat through its Python
API, the pip package `metacat-client`; the transfer step uses `globus-cli`.
Neither ships with the NERSC Python, so they have to be pip-installed
somewhere (`requirements.txt`). A venv is just the least intrusive place:

```
python3 -m venv ~/.amsc-fetch/venv
~/.amsc-fetch/venv/bin/pip install -r requirements.txt
```

`pip install --user -r requirements.txt` into the NERSC python module's user
site works too; then `[venv].python` in `catalog.toml` is just that
interpreter. Whatever you use, keep it on a filesystem that is not purged; a
venv on NERSC `$SCRATCH` silently loses files after 8 weeks.

Catalog reads are anonymous: querying, `--diff`, `--list-ensembles` and
`--paths` need no account anywhere. Writing needs a MetaCat token (valid
about a week), and on the AmSC server every login method (password, x509,
SciToken) resolves to an **FNAL Services identity** holding the `lqcd_write`
role:

```
bin/metacat_login.sh [fnal-services-username]   # interactive password login
bin/check_auth.sh                                # exit 0/2/3/4, see header
```

Groups without an FNAL account can still use the first two steps: build
`records.json` from their own inventory and TOML, then hand that file to
someone with write access to run `declare_records.py`. Whether `lqcd_write`
can be granted to non-FNAL identities is an open question for the AmSC team.

## Running it for one ensemble

```
PY=$(python3 -c 'import tomllib;print(tomllib.load(open("catalog.toml","rb"))["venv"]["python"])')
ENS=ensembles/cl3_32_64_b6p5_m0p1788.toml

$PY scripts/hpss_inventory.py $ENS                 # ~minutes; metadata-only HPSS ls
$PY scripts/build_records.py  $ENS                 # instant, offline; review work/<name>/records.json
$PY scripts/declare_records.py work/cl3_32_64_b6p5_m0p1788/records.json --dry-run   # server validates
$PY scripts/declare_records.py work/cl3_32_64_b6p5_m0p1788/records.json --limit 5   # smoke test
$PY scripts/declare_records.py work/cl3_32_64_b6p5_m0p1788/records.json             # the rest
$PY scripts/retrieve_records.py cl3_32_64_b6p5_m0p1788 --diff work/cl3_32_64_b6p5_m0p1788/records.json
```

`declare_records.py` is idempotent: it looks up what is already in the
namespace and only declares the rest, so a failed batch is just re-run. Use
`--update-existing` after editing the TOML to push new metadata onto records
that are already there (merge, never replace, so FNAL-stamped fields survive).

`retrieve_records.py --diff` is the round-trip test: it exits 0 only when
every intended record is in the catalog with the intended size and metadata.

## Adding an ensemble

1. Copy `ensembles/cl3_32_64_b6p5_m0p1788.toml`, change `[ensemble]`,
   `[lqcd]`, `[streams.*]`. Take numbers from the HMC input XML, not from
   memory; the file header says where the originals are.
2. If the stream directories have a different layout, adjust the `[[files]]`
   regexes. `build_records.py` prints what it could not classify.
3. Run the four steps.

## Querying what is there (any MetaCat client, no token)

```
metacat query -j -m 'files from lqcd:cl3_32_64_b6p5_m0p1788 where lqcd.artifact.kind = "gauge_config" and lqcd.stream = "a" and lqcd.trajectory >= 1000'
```

or `scripts/retrieve_records.py cl3_32_64_b6p5_m0p1788 --kind gauge_config --stream a --trajectory-min 1000 --paths`
to get the HPSS paths for `hsi get` or a Globus batch transfer from the
`NERSC HPSS` collection named in each record's `AmSC.common.location`.

## Layout

```
catalog.toml            site config: catalog, namespace, storage collections, interpreter
requirements.txt        metacat-client, globus-cli
ensembles/<name>.toml   one per ensemble (physics, streams, file rules)
scripts/                the four catalog steps + catalog_common.py, and the three
                        data-movement steps hpss_select / globus_transfer / untar_manifest
bin/metacat_login.sh    interactive token login;  bin/check_auth.sh  status + exit codes
bin/untar_slurm.sh      sbatch wrapper for the unpack step
docs/schema.md          every field, what is required, mass/kappa convention
docs/adding_an_ensemble.md
docs/storage_and_transfer.md   Globus collections, HPSS, FDP backup plan, checksums
docs/data_movement.md   select -> Globus transfer -> unpack workflow, with a worked example
docs/correlator_reduction.md   read datasets out of htar archives in place (htar_members / reduce_corrs / merge_corrs)
docs/open_questions.md  what we want reviewers' opinions on
work/                   generated per-ensemble JSON (gitignored)
```

## Status / roadmap

- [x] cl3_32_64_b6p5_m0p1788: 4 streams, 1242 configs, 2502 records, 1.4 TB on HPSS,
      declared to `lqcd:cl3_32_64_b6p5_m0p1788` 2026-10-14 and round-trip verified (0 diffs)
- [ ] remaining cl3 ensembles (`ensembles/*.toml`, one per ensemble, streams grouped)
- [ ] cl21 ensembles
- [ ] sparse propagators and correlators (new `lqcd.artifact.kind` values and
      `lqcd.parent.*` provenance back to the gauge configs)
- [ ] checksums via a staging pass or a Globus transfer with verify
