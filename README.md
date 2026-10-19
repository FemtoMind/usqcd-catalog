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

Site-specific values (catalog URL, namespace, storage Globus UUID, HPSS root)
live in `catalog.toml`. Ensemble-specific values live in
`ensembles/<name>.toml`. Nothing in `scripts/` hard-codes either.

## Where to see the LQCD metadata

* **MetaCat GUI** (datasets, free-form queries): https://metacat.fnal.gov:9443/amsc_meta_prod/app/gui
  (FNAL Services login)
* **Central AmSC catalog** (OpenMetadata): https://openmetadata-staging.american-science-cloud.org/container/fnal-amsc-storage.fnal-amsc-data-catalog
  (Globus login)
* **No login**: the REST API returns JSON, e.g. the first ensemble's dataset record
  https://metacat.fnal.gov:9443/amsc_meta_prod/app/data/dataset?dataset=lqcd:cl3_32_64_b6p5_m0p1788
  and any MQL query; see `docs/browsing.md`.

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
* **Checksums are optional.** HPSS stores none and the bytes are on tape
  only, so records start without them. `build_records.py --checksums` takes
  hashes you already have; `attach_checksums.py --from-dcache` pulls the
  checksum FDP's dCache computes on ingest once a copy lives there.
* **Names follow FDP's rules** (unique per namespace; letters, digits,
  `-._/` only; no `test` in a namespace that should reach the central
  catalog). `build_records.py` enforces them.

Full field list: `docs/schema.md`.

## Setup

Every tool is a plain script: `python3 scripts/<name>.py ...`. The paths in
this README are relative to the repo for brevity, but absolute paths work
from any directory (`python3 /path/to/usqcd-catalog/scripts/build_records.py
...`); each script finds `catalog_common.py`, `catalog.toml` and `work/`
relative to its own location. `pyproject.toml` is dependency-only: one
`pip install -e .` against whatever `python3` you use gives that interpreter
`metacat-client` (plus `tomli` on Python < 3.11) and installs nothing else.
No venv is required:

```
module load python          # NERSC: the OS python3 is 3.6; this one is 3.13
pip install --user -e .     # metacat-client (+ tomli on python < 3.11)
python3 scripts/hpss_inventory.py ensembles/cl3_32_64_b6p5_m0p1788.toml
```

A venv works the same way if you prefer isolation
(`python3 -m venv .venv && source .venv/bin/activate && pip install -e .`;
`.venv/` is gitignored).
`pip install -e '.[transfer]'` adds globus-cli for moving data and
`'.[reduce]'` adds h5py + numpy for the correlator scripts (both already in
the NERSC python module). `requirements.txt` is the same dependency list for
`pip install -r`.

| scripts | need |
|---|---|
| hpss_inventory, build_records, hpss_select, untar_manifest | stdlib only |
| declare_records, retrieve_records, attach_checksums | metacat-client |
| globus_transfer | globus-cli for `--submit` / `--status` |
| htar_members, reduce_corrs, merge_corrs | h5py + numpy (`.[reduce]`, or NERSC `module load python`) |

The Slurm wrappers in `bin/` do `module load python` and use `python3`; the
shell helpers that need metacat-client (`bin/check_auth.sh`,
`bin/metacat_login.sh`) and `bin/untar_slurm.sh` use `[venv].python` from
`catalog.toml` instead when it is set. If you do use a venv, keep it on a
filesystem that is not purged; one on NERSC `$SCRATCH` silently loses files
after 8 weeks.

## Authentication

Catalog reads are anonymous: querying, `--diff`, `--list-ensembles` and
`--paths` need no account anywhere. Writing to the catalog needs an FNAL
Services account with the `lqcd_write` role; moving data needs a Globus
login plus per-collection consents. `docs/authentication.md` walks through
getting each one. Day to day:

```
bin/metacat_login.sh <fnal-services-username>   # MetaCat token, valid about a week
bin/check_auth.sh                                # exit 0/2/3/4, see header
```

Groups without an FNAL account can still use the first two steps: build
`records.json` from their own inventory and TOML, then hand that file to
someone with write access to run `declare_records.py`.

## Running it for one ensemble

```
module load python          # or activate the venv you installed into
ENS=ensembles/cl3_32_64_b6p5_m0p1788.toml
W=work/cl3_32_64_b6p5_m0p1788

python3 scripts/hpss_inventory.py  $ENS                      # minutes; metadata-only HPSS ls -> $W/inventory.json
python3 scripts/build_records.py   $ENS                      # instant, offline; review $W/records.json
python3 scripts/declare_records.py $W/records.json --dry-run # local checks (+ server checks once the dataset exists)
python3 scripts/declare_records.py $W/records.json --limit 5 # creates the dataset, declares 5 files
python3 scripts/retrieve_records.py cl3_32_64_b6p5_m0p1788 --diff $W/records.json
python3 scripts/declare_records.py $W/records.json           # the rest
python3 scripts/retrieve_records.py cl3_32_64_b6p5_m0p1788 --diff $W/records.json   # must print OK
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

or `python3 scripts/retrieve_records.py cl3_32_64_b6p5_m0p1788 --kind gauge_config --stream a --trajectory-min 1000 --paths`
to get the HPSS paths for `hsi get` or a Globus batch transfer from the
`NERSC HPSS` collection named in each record's `AmSC.common.location`.
`retrieve_records.py --list-ensembles` shows every ensemble with its physics
summary, and `retrieve_records.py --kind gauge_config --beta 6.5 --kappa 0.1308`
searches across ensembles (mass and kappa both match either way of storing it).

## Layout

```
catalog.toml            site config: catalog, namespace, storage collections, optional interpreter
pyproject.toml          dependencies for `pip install -e .` (metacat-client; extras [transfer], [reduce])
requirements.txt        the same list for `pip install -r`
ensembles/<name>.toml   one per ensemble (physics, streams, file rules)
scripts/                the four catalog steps + catalog_common.py (+ attach_checksums.py), the three data-movement
                        steps hpss_select / globus_transfer / untar_manifest, and the three
                        correlator steps htar_members / reduce_corrs / merge_corrs
bin/metacat_login.sh    interactive token login;  bin/check_auth.sh  status + exit codes
bin/untar_slurm.sh      sbatch wrapper for the unpack step;  bin/reduce_corrs_xfer.sh  for reduce_corrs
bin/htar_extract.sh     pull named members out of an htar archive;  bin/wait_job.sh  block on a Slurm job
docs/authentication.md  accounts, tokens, Globus consents, step by step for a new user
docs/schema.md          every field, what is required, mass/kappa convention
docs/adding_an_ensemble.md
docs/storage_and_transfer.md   Globus collections, HPSS, FDP backup plan, checksums
docs/data_movement.md   select -> Globus transfer -> unpack workflow, with a worked example
docs/correlator_reduction.md   read datasets out of htar archives in place (htar_members / reduce_corrs / merge_corrs)
docs/open_questions.md  what we want reviewers' opinions on
docs/browsing.md        looking at records from a browser (REST URLs, dCache listing)
work/                   generated per-ensemble JSON (gitignored)
```

## Status / roadmap

- [x] cl3_32_64_b6p5_m0p1788: 4 streams, 1242 configs, 2502 records, 1.4 TB on HPSS,
      declared to `lqcd:cl3_32_64_b6p5_m0p1788` 2026-10-14 and round-trip verified (0 diffs)
- [ ] remaining cl3 ensembles (`ensembles/*.toml`, one per ensemble, streams grouped)
- [ ] cl21 ensembles
- [ ] sparse propagators and correlators (new `lqcd.artifact.kind` values and
      `lqcd.parent.*` provenance back to the gauge configs)
- [ ] checksums: back up to FDP, then `attach_checksums.py --from-dcache`
