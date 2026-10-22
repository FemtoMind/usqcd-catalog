# usqcd-catalog

Scripts for cataloging and discovering USQCD lattice-QCD data — gauge-field ensembles and reusable building blocks for quark propagators —  in the AmSC Fermi Data Platform MetaCat catalog.

Discovery is one script and needs no account (`retrieve_records.py`, below).
The catalog itself is the ensemble registry: each ensemble's dataset record
holds its physics, streams, file rules and provenance, and
`ensembles/*.toml` are snapshots generated from it. Registering an ensemble
or a stream needs catalog write access, not write access to this repository.
Cataloging files is an inventory and three small scripts with JSON between
them, so every step can be inspected and re-run on its own:

```
register_ensemble.py my.toml        add_stream.py --ensemble X --stream Y        update_ensemble.py --ensemble X --set ...
   (once per ensemble)                  (once per stream)                            (parameters, observables)
        └───────────────────────────────────►  catalog registry entry  ──►  ensembles/<name>.toml (snapshot, kept in sync)
                                                       │
1. an inventory of the files     ──►  work/<name>/inventory.json   (NERSC HPSS: scripts/hpss_inventory.py)
2. scripts/build_records.py      registry entry + inventory ──►  work/<name>/records.json
3. scripts/declare_records.py    records.json ──► MetaCat
4. scripts/retrieve_records.py   MetaCat ──► discovery, and --diff records.json for the round trip
```

The catalog covers gauge-field ensembles and the propagator building blocks
computed on them. Site-specific values (catalog URL, namespace, storage
Globus UUIDs, archive roots) live in `catalog.toml`; nothing in `scripts/`
hard-codes them. Every script keeps the local snapshots in `ensembles/` and
`derived_datasets/` current with the catalog, so they are never edited or
synced by hand. Several groups writing to the same registry is covered in
[docs/registry.md](docs/registry.md): names and stream labels are permanent
and unique, other groups' entries need `--force`, and writers refuse to
overwrite an entry someone else changed in the meantime.

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

Writing to the catalog needs an FNAL Services account with the `lqcd_write`
role (see Authentication). Groups without one can run the build step and
hand `records.json` to someone who has it.

Register the ensemble, once. Copy `templates/ensemble.toml`, fill in
`[ensemble]`, `[lqcd]` and `[streams.*]`, and check it with `--dry-run`
first. Take numbers from the HMC input, not from memory
(`scripts/lime_hmc_params.py` reads Chroma's input back out of a LIME
header); comments in the file are kept as provenance. This is the only time
a description is written by hand: the registered copy is
`ensembles/<name>.toml`, generated from the catalog, and your input file is
no longer needed.
```
python3 scripts/register_ensemble.py my_ensemble.toml
```
List the files into `work/<name>/inventory.json`, one entry per file with
its path, size, date and owner, grouped by stream (format in
`docs/adding_an_ensemble.md`). On NERSC HPSS this script builds it from a
metadata-only `hsi ls -lR` in minutes per TB; the same JSON written from a
`find`, an object-store listing or a Globus `ls` serves any other storage.
```
python3 scripts/hpss_inventory.py ensembles/<name>.toml
```
Build the file records, offline apart from one catalog read that picks up
the current registry entry. It prints any files no `[[files]]` rule
matched; a different directory layout means adjusting those regexes (see
Adding physics information).
```
python3 scripts/build_records.py ensembles/<name>.toml
```
Declare them. This is idempotent: it looks up what is already in the
namespace and declares only the rest, so a failed batch is just re-run.
`--dry-run` runs every check without writing, and `--limit N` declares only
the first N records, a good first push before declaring tens of thousands.
```
python3 scripts/declare_records.py work/<name>/records.json
```
Check the round trip. It exits 0 only when every intended record is in the
catalog with the intended size and metadata, and lists what is missing,
extra or different otherwise.
```
python3 scripts/retrieve_records.py <name> --diff work/<name>/records.json
```

## Adding a stream

A new Markov chain stream of a registered ensemble needs no file editing.
The script refuses an existing stream label (labels are permanent), a
directory another stream already uses, and an ensemble registered by
another group unless `--force`. `--dir` defaults to `<name>-<label>`; add
`--parent-stream` and `--parent-trajectory` when the stream was branched
from another, and check with `--dry-run` first.
```
python3 scripts/add_stream.py --ensemble <name> --stream <label> --dir <directory>
```
Then catalog its files with the inventory, build, declare and diff steps
above. Existing streams are untouched: only the new files are declared.

## Adding physics information

Parameters, observables, description text and file rules of a registered
ensemble are changed in place, without editing any file. Each `--set` takes
a dotted path using the same names as the description's tables; values are
read as TOML (`16`, `0.3042`, `true`, `"text"`). `--merge` adds a whole TOML
fragment, such as a new `[observables.w0]` table.
```
python3 scripts/update_ensemble.py --ensemble <name> --set observables.mpi.value=0.3042 --set observables.mpi.stat=0.0001
```
```
python3 scripts/update_ensemble.py --ensemble <name> --merge new_observables.toml
```
The script starts from the catalog's current state, so two people updating
different fields don't overwrite each other, and `--dry-run` shows the
change first. Streams can't be changed this way, and names are permanent.
If the change touches physics that every file record also carries (an
action parameter, say), the script says so; rebuild and declare with
`--update-existing` to propagate it to the files.

## Adding derived datasets

Derived datasets are the propagator building blocks computed on an
ensemble's configurations: sparse propagators, both timeslice-to-all and
all-to-all, distillation preambulators, etc.
Each is its own dataset,
`lqcd:<ensemble>_<product>`, whose records point back at the configuration
they were computed on. Correlators and other measurement-level data are out
of scope for this catalog.

Register one from `templates/derived_dataset.toml`, which describes an
all-to-all set; its parent ensemble must already be registered. It inherits
the parent's physics and streams, and its snapshot lands in
`derived_datasets/`.
```
python3 scripts/register_ensemble.py my_a2a_props.toml
```
Then build, declare and diff as for an ensemble; it reuses the parent's
inventory. Where archive names don't say what an archive holds (source
timeslices, flavors, smearing), `scripts/sprop_members.py` reads every htar
index first, and the build records what each archive actually contains;
see `docs/adding_an_ensemble.md`.
```
python3 scripts/build_records.py derived_datasets/<name>.toml
```

## Layout

```
catalog.toml            site config: catalog, namespace, storage collections, optional interpreter
pyproject.toml          dependencies for `pip install -e .` (metacat-client; extras [transfer], [reduce])
requirements.txt        the same list for `pip install -r`
ensembles/<name>.toml   registry snapshot per gauge ensemble, generated from the catalog and kept current by every script (do not hand-edit)
derived_datasets/       the same for derived products (sparse propagator sets, ...), each naming its parent
templates/              starting points for register_ensemble.py (ensemble, derived dataset)
scripts/                registry: register_ensemble, add_stream, update_ensemble (+ registry.py, and sync_registry.py,
                        which the others call and CI can run with --check);
                        the four catalog steps + catalog_common.py (+ attach_checksums.py,
                        lime_hmc_params.py to read HMC parameters out of a LIME header,
                        sprop_members.py to read what propagator archives hold from their htar
                        indexes, survey_listing.py to summarise a listing), the three data-movement
                        steps hpss_select / globus_transfer / untar_manifest, and the three
                        correlator reduction steps htar_members / reduce_corrs / merge_corrs
                        (a data-movement utility; correlators themselves are not catalogued)
bin/metacat_login.sh    interactive token login;  bin/check_auth.sh  status + exit codes
bin/untar_slurm.sh      sbatch wrapper for the unpack step;  bin/reduce_corrs_xfer.sh  for reduce_corrs;
                        bin/sprop_members_xfer.sh  for sprop_members (submit from the repo root)
bin/htar_extract.sh     pull named members out of an htar archive;  bin/wait_job.sh  block on a Slurm job
docs/authentication.md  accounts, tokens, Globus consents, step by step for a new user
docs/schema.md          every field, what is required, mass/kappa convention
docs/adding_an_ensemble.md
docs/registry.md        the catalog as shared registry: collisions, ownership, stale copies, git conflicts
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
