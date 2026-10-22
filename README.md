# usqcd-catalog

Scripts for cataloging and discovering USQCD lattice-QCD data — gauge-field ensembles and reusable building blocks for quark propagators —  in the AmSC Fermi Data Platform MetaCat catalog.

Discovery is one script and needs no account (`retrieve_records.py`, below).
Cataloging is a hand-written description of the ensemble, an inventory of
its files, and three small scripts with JSON between them, so every step
can be inspected and re-run on its own:

```
ensembles/<name>.toml  (hand-written: physics, streams, file rules)
        │
        ▼
1. an inventory of the files     ──►  work/<name>/inventory.json   (NERSC HPSS: scripts/hpss_inventory.py)
2. scripts/build_records.py      TOML + inventory ──►  work/<name>/records.json
3. scripts/declare_records.py    records.json ──► MetaCat   (the only write step)
4. scripts/retrieve_records.py   MetaCat ──► discovery, and --diff records.json for the round trip
```

Site-specific values (catalog URL, namespace, storage Globus UUIDs, archive
roots) live in `catalog.toml`. Ensemble-specific values live in
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
* **Downstream observables live on the dataset record** (`lqcd.obs.*`): flow
  scales t0 and w0, pion and nucleon masses, and the lattice spacing in named
  schemes (t0, w0) each with definition, value, statistical and scale errors.
  They come from an `[observables]` table in the ensemble TOML and are pushed
  with `declare_records.py --update-existing --dataset-only`.
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
python3 scripts/retrieve_records.py --summary   # anonymous; prints the table at the end of this page
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
| hpss_inventory, build_records, hpss_select, untar_manifest, lime_hmc_params | stdlib only |
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

## Data discovery

Reads are anonymous; nothing in this section needs an account.

Summarize available gauge-field ensembles
```
python3 scripts/retrieve_records.py --summary              # every ensemble: Nf, streams, configurations (the table below)
```
List available ensemble details
```
python3 scripts/retrieve_records.py --list-ensembles       # one line per ensemble with beta, volume, masses, a, m_pi
```
Find derived observables (e.g. pion mass, Wilson-flow scales)
```
python3 scripts/retrieve_records.py --observables          # t0, w0, lattice spacing per scheme, m_pi, M_N per ensemble
```
List derived datasets (sparse propagator sets) with their record counts and sizes
```
python3 scripts/retrieve_records.py --summary --derived    # the table below plus the datasets that point at each ensemble
```
Find filesystem paths for trajectories in a specific ensemble
```
python3 scripts/retrieve_records.py cl3_32_64_b6p5_m0p1788 --kind gauge_config --stream a --trajectory-min 1000 --paths
```
Discover gauge-field configurations with particular action parameter values
```
python3 scripts/retrieve_records.py --kind gauge_config --beta 6.5 --kappa 0.1308   # across ensembles
```

Filters: `--kind` (`gauge_config`, `sparse_propagator_timeslice_to_all`,
`sparse_propagator_all_to_all`, `hmc_input`, ...), `--stream`,
`--trajectory` / `--trajectory-min` / `--trajectory-max`, `--beta`, and
`--mass` or `--kappa` (either matches records stored the other way; `--flavor`
and `--tol` adjust them). 

Every hit is a self-describing record: the
ensemble's physics plus `lqcd.storage.path`, the path on the storage system,
and `AmSC.common.location`, a `globus://<collection>/<path>` URL.

`--paths` gives a list to feed `hsi get`, a Globus batch transfer, or
whatever moves data at the site the bytes are on; the full dump lands in
`work/<ensemble>/catalog_dump.json`. 

`--mql` runs a raw MetaCat query
instead (so does `metacat query -j -m '...'` from the MetaCat CLI), the
browser routes are in `docs/browsing.md`, and the field names in
`docs/schema.md`.

## Adding an ensemble

Cataloging is a hand-written description, an inventory of the files, and
three scripts with JSON between them, so every stage can be inspected and
re-run on its own:

```
python3 scripts/build_records.py    ensembles/<name>.toml                   # TOML + inventory -> work/<name>/records.json (offline)
python3 scripts/declare_records.py  work/<name>/records.json                # the only write to MetaCat
python3 scripts/retrieve_records.py <name> --diff work/<name>/records.json  # round trip; exit 0 when the catalog matches
```

1. **Describe the ensemble** in `ensembles/<name>.toml`: copy
   `ensembles/cl3_32_64_b6p5_m0p1788.toml` and change `[ensemble]`, `[lqcd]`
   and `[streams.*]`. Take numbers from the HMC input, not from memory
   (`scripts/lime_hmc_params.py` reads Chroma's input back out of a LIME
   header). If the stream directories have a different layout, adjust the
   `[[files]]` regexes; `build_records.py` prints what it could not classify.
2. **List the files** into `work/<name>/inventory.json`: one entry per file
   with its path, size, date and owner, grouped by stream (format in
   `docs/adding_an_ensemble.md`). The worked example is NERSC HPSS, where
   `python3 scripts/hpss_inventory.py ensembles/<name>.toml` builds it from a
   metadata-only `hsi ls -lR` in minutes per TB; the same JSON written from a
   `find`, an object-store listing or a Globus `ls` serves any other storage.
3. **Build, declare, check.** `declare_records.py` is idempotent: it looks up
   what is already in the namespace and only declares the rest, so a failed
   batch is just re-run. `--dry-run` runs the local checks (names, collisions,
   required fields) and, once the dataset exists, the server's validation,
   without writing; `--limit N` creates the dataset and declares only the
   first N records, a good first push before declaring tens of thousands;
   `--update-existing` pushes new metadata from an edited TOML onto records
   that are already there (merge, never replace, so FNAL-stamped fields
   survive), and `--dataset-only` restricts that to the dataset record. The
   `--diff` lists what is missing, extra or different, and is the test that
   the catalog says what you meant.

Groups without write access stop after the build and hand `records.json` to
someone who has it (see Authentication). Derived products (propagators,
correlators) get their own description in `derived_datasets/` with
`parent = "<ensemble>"`; see `docs/adding_an_ensemble.md`.

## Layout

```
catalog.toml            site config: catalog, namespace, storage collections, optional interpreter
pyproject.toml          dependencies for `pip install -e .` (metacat-client; extras [transfer], [reduce])
requirements.txt        the same list for `pip install -r`
ensembles/<name>.toml   one per gauge ensemble (physics, streams, file rules)
derived_datasets/       one per derived product (sparse propagator sets, ...), each naming its parent ensemble
scripts/                the four catalog steps + catalog_common.py (+ attach_checksums.py,
                        lime_hmc_params.py to read HMC parameters out of a LIME header,
                        sprop_members.py to read what propagator archives hold from their htar
                        indexes, survey_listing.py to summarise a listing), the three data-movement
                        steps hpss_select / globus_transfer / untar_manifest, and the three
                        correlator steps htar_members / reduce_corrs / merge_corrs
bin/metacat_login.sh    interactive token login;  bin/check_auth.sh  status + exit codes
bin/untar_slurm.sh      sbatch wrapper for the unpack step;  bin/reduce_corrs_xfer.sh  for reduce_corrs;
                        bin/sprop_members_xfer.sh  for sprop_members (submit from the repo root)
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

## USQCD Ensembles

One dataset per ensemble (`lqcd:<ensemble>`) holding every stream's
configurations, plus one dataset per derived product
(`lqcd:<ensemble>_<product>`, today sparse propagators) pointing back at it.
Configurations are counted across all streams; 0 means the configurations
have no catalogued storage location yet. Regenerate with
`python3 scripts/retrieve_records.py --summary` (`--derived` adds the
propagator datasets).

| ensemble | Nf | streams | configs | size |
|---|---|---|---|---|
| `cl21_48_96_b6p3_m0p2416_m0p2050` | 2+1 | 8 | 5456 | 30.3 TB |
| `cl21_64_128_b6p3_m0p2416_m0p2050` | 2+1 | 4 | 3128 | 54.9 TB |
| `cl21_64_192_b6p5_m0p2080_m0p1788` | 2+1 | 2 | 1422 | 37.5 TB |
| `cl21_72_192_b6p5_m0p2091_m0p1778` | 2+1 | 4 | 2201 | 82.6 TB |
| `cl21_72_192_b6p7_m0p1843_m0p1640` | 2+1 | 1 | 1132 | 42.4 TB |
| `cl21_96_192_b6p5_m0p2095_m0p1793` | 2+1 | 2 | 1165 | 103.5 TB |
| `cl3_16_48_b6p1_m0p2450` | 3 | 8 | 32080 | 3.3 TB |
| `cl3_24_48_b6p1_m0p2450` | 3 | 16 | 16218 | 5.7 TB |
| `cl3_30_60_b6p2_m0p2200` | 3 | 1 | 685 | 602.4 GB |
| `cl3_30_60_b6p2_m0p2250` | 3 | 1 | 662 | 583.1 GB |
| `cl3_32_48_b6p1_m0p2450` | 3 | 3 | 12735 | 9.3 TB |
| `cl3_32_64_b6p5_m0p1788` | 3 | 4 | 1242 | 1.4 TB |
| `cl3_36_64_b6p3_m0p2050` | 3 | 1 | 735 | 1.2 TB |
| `cl3_36_64_b6p3_m0p2170` | 3 | 1 | 520 | 840.2 GB |
| `cl3_48_64_b6p1_m0p2450` | 3 | 0 | 0 | 0 |
| `cl3_48_64_b6p3_m0p2050` | 3 | 0 | 0 | 0 |
| `cl3_48_96_b6p5_m0p1788` | 3 | 1 | 816 | 4.6 TB |

17 ensembles, 80197 configurations, 378.8 TB  
18 derived datasets, 76891 records, 6.1 PB  
Namespace `lqcd`, 2026-10-20.
