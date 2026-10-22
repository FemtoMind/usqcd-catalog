# Adding an ensemble

Everything an ensemble needs is one TOML description, registered once in
the catalog, which is the registry (`docs/registry.md`). The scripts never
need editing for a new ensemble; if they do, that is a bug in the template,
please report it.

## 1. Write a description and register it

Copy `templates/ensemble.toml` (or, for a fuller worked example, any file in
`ensembles/`), edit it, then

```
python3 scripts/register_ensemble.py my_ensemble.toml --dry-run   # every check, nothing written
python3 scripts/register_ensemble.py my_ensemble.toml
```

The name must be new (names are permanent; an existing one is refused with
who registered it). The `#` comments become `[ensemble] provenance`, so say
where each number came from. Registration writes the dataset record and
generates `ensembles/<name>.toml` from it; that snapshot is the copy to keep
and build from, your input file is no longer needed. The file has four
tables; only the first is required.

**Why TOML.** The description is a hand-written
document, and the file header is where we keep the provenance ("beta and
csw taken from restart_10000.xml of stream a"): TOML allows comments, JSON
does not. TOML also writes nested tables as headings (`[lqcd.action]`),
which flatten straight to the dotted catalog keys (`lqcd.action.csw`), and
takes regexes as literal strings (`'^cfgs/.+_cfg_(?P<trajectory>\d+)\.lime$'`)
without doubling every backslash. One syntax only, so there is one thing to
learn and one thing to validate. Everything the scripts *generate*
(inventory, records, dumps) is JSON.

### `[ensemble]` (required: `name`)

| key | meaning |
|---|---|
| `name` | ensemble name; becomes the dataset `<namespace>:<name>` and `lqcd.ensemble` on every file |
| `display_name` | short label for catalog UIs |
| `description` | prose; the only thing the FDP web UI renders, so say what a physicist would want to know |
| `storage` | key into `catalog.toml` `[storage.*]` saying where the files are now |
| `collaboration`, `generated_by`, `machine`, `code`, `year` | provenance, all optional |

### `[lqcd]` (optional)

Physics and algorithm parameters. Nested tables become dotted keys:
`[lqcd.action] csw = 1.17` is stored as `lqcd.action.csw`. Use whatever your
action needs; `docs/schema.md` lists the names already in use so different
groups converge on the same spelling. Copy numbers from the generation code's
input file, not from a paper, and do not round.

Mass or kappa: give either `[lqcd.mass]` or `[lqcd.kappa]` per flavor
(`light`, `strange`, `charm`...). For isotropic Wilson-type actions the other
is derived and both are stored.

**Where to get the numbers.** Chroma writes its full HMC input into the
header of every LIME configuration it saves, so the file itself is the
authoritative source even when no restart XML survives:

```
python3 scripts/lime_hmc_params.py hpss:/home/w/.../cfgs/<ens>_cfg_1000.lime --toml
```

prints a `[lqcd]` block to paste (beta, u0, csw, stout parameters, tau0,
integrator, save interval, the distinct masses in the input). It reads only
the first few MB; for an HPSS file that must already be on the disk cache
(`hsi ls -X <file>`), otherwise HPSS stages the whole file first.

### `[observables]` (optional)

Downstream numbers that describe the ensemble, stored on the dataset record
as `lqcd.obs.*` (field list in `docs/schema.md`). One sub-table per
observable with `quantity`, `value`, `stat`, optional `ncfgs` and `notes`;
`[observables.a]` lists `schemes` and has one sub-table per scheme with
`definition`, `value`, `stat`, `scale_err`. See any `ensembles/cl3_*.toml`
for the full pattern. After editing, push without touching the file records:

```
python3 scripts/build_records.py   ensembles/<name>.toml
python3 scripts/declare_records.py work/<name>/records.json --update-existing --dataset-only
```

### `[streams]` (optional; default one stream `main` in directory `<name>`)

One sub-table per Markov chain stream. The key is the stream label stored in
`lqcd.stream`; `dir` is the directory under the storage root. Add
`parent_stream` / `parent_trajectory` when you know where a stream was
branched from; omit them otherwise.

```toml
[streams.main]
dir = "cl3_32_64_b6p5_m0p1788"
[streams.a]
dir = "cl3_32_64_b6p5_m0p1788-a"
parent_stream = "main"
parent_trajectory = 1500
exclude = ['^cfgs/cl3_32_64_b6p5_m0p1788_cfg_1500\.lime$']   # optional: files in this
                                                           # directory to leave out, e.g. a
                                                           # copy of the branch-point config
```

Stream labels are free strings; NPLQCD's `-1000` style suffix (branched from
`main` at trajectory 1000) becomes `[streams.1000]` with
`parent_trajectory = 1000`, and `-djm-1` becomes `[streams."djm-1"]`.

### `[[files]]` (optional; default = NPLQCD/Chroma layout)

Ordered regex rules matched against each file's path relative to the stream
directory. `kind` becomes `lqcd.artifact.kind`; a `(?P<trajectory>\d+)` group
becomes `lqcd.trajectory`. Unmatched files are listed by `build_records.py`
and skipped, so start permissive and tighten.

```toml
[[files]]
pattern = '^cfgs/.+_cfg_(?P<trajectory>\d+)\.lime$'
kind = "gauge_config"
format = "application/x-lime"
description = "Gauge configuration in SciDAC/ILDG LIME format."
```

### Three storage layouts seen so far

* **One file per configuration** (`cl3_32_64_b6p5_m0p1788`, `cl3_24_48_b6p1_m0p2450`):
  one `[streams.<s>]` per directory, rules with a `trajectory` group.
* **Plain tar archives named by trajectory range** (`cl3_32_48_b6p1_m0p2450`,
  `cfgs/<ens>_cfg_10000-19990.tar`): kind `gauge_config_archive`, rule groups
  `trajectory_min` and `trajectory_max`.
* **One htar archive per stream in a shared directory** (`cl3_16_48_b6p1_m0p2450`,
  `cfgs/a.tar` ... `h.tar` plus `.idx`): a single `[streams.shared]` entry for
  the directory, a rule with a `(?P<stream>[a-h])` group that sets each
  file's stream label, and the trajectory range read from the htar index:

  ```
  python3 scripts/htar_members.py --archives .../cfgs/{a..h}.tar --stream-depth 0 \
      --pattern '^[a-h]/.+_cfg_(?P<trajectory>\d+)\.lime$' -o work/<name>/members.json
  python3 scripts/build_records.py ensembles/<name>.toml --archive-index work/<name>/members.json
  ```

## Derived datasets (propagator building blocks)

Derived datasets are the propagator building blocks computed on an
ensemble's configurations, timeslice-to-all and all-to-all sparse
propagators so far; correlators and other measurement-level data are out of
scope. A product gets its own registry entry and dataset,
snapshot in `derived_datasets/<ens>_<product>.toml` (separate from
`ensembles/`, which holds only gauge ensembles). Start from
`templates/derived_dataset.toml` and register it with `register_ensemble.py`
like an ensemble; its parent must already be registered. The description has

```toml
[ensemble]
name = "cl3_48_96_b6p5_m0p1788_sparseprops"
parent = "cl3_48_96_b6p5_m0p1788"     # inherits [lqcd] and [streams] from ensembles/<parent>.toml
kind = "sparse_propagator_set"        # dataset-level lqcd.artifact.kind
name_style = "path"                   # catalog names "<stream dir>/<path>", since 1000.tar repeats per subdir

[derived.prop]                        # facts about the product, flattened to lqcd.prop.* on every record
smearing_width = 6.0
smearing_steps = 150
# ...

[[files]]
pattern = '^sparseprops-t(?P<tsrc>\d+)/(?P<trajectory>\d+)\.tar$'   # extra groups -> lqcd.prop.tsrc
kind = "sparse_propagator_archive"
parent_name = "cl3_48_96_b6p5_m0p1788_cfg_{trajectory}.lime"       # -> lqcd.parent.name / .did
meta = { "lqcd.prop.variant" = "chroma-props" }                    # constants for this rule
```

`build_records.py` reuses the parent's `work/<parent>/inventory.json`, so no
new HPSS listing is needed. Get the smearing and solver numbers from the
run's own XML (see the sparseprops TOML header for where they were read),
not from memory. `docs/schema.md` lists the resulting fields.

When archive names do not say what the archives hold (source timeslice
chosen per configuration, several smearing layouts in one directory), read
the htar indexes once and build with them:

```
sbatch bin/sprop_members_xfer.sh work/<parent>/inventory.json 'sparseprops.*/[0-9]+\.tar$' \
    work/<name>/members.json 6                      # xfer queue; ~1 s per archive, resumable
python3 scripts/build_records.py derived_datasets/<name>.toml --archive-index work/<name>/members.json
```

Each indexed record then carries `lqcd.prop.tsrc_list`, `flavors`,
`smearing_combinations` and `n_members` read from its own archive.

## 2. Inventory the files

`build_records.py` reads `work/<name>/inventory.json`: the files of every
stream, with their sizes, dates and owners. It does not care how that list
was made.

```json
{
  "ensemble": "<name>", "storage": "nersc_hpss", "root": "/home/w/wdetmold/clover-iso",
  "generated_at": "2026-10-19T17:06:23Z",
  "streams": {
    "main": {"dir": "<name>", "files": [
      {"path": "/home/w/wdetmold/clover-iso/<name>/cfgs/<name>_cfg_10.lime",
       "rel_path": "cfgs/<name>_cfg_10.lime", "size": 6115309384,
       "mtime": "2024-07-19", "owner": "wdetmold"}
    ]}
  }
}
```

One entry per file; `rel_path` (relative to the stream directory) is what
the `[[files]]` regexes match, `path` becomes `lqcd.storage.path` and the
`globus://` location, and `storage` names the `[storage.*]` table in
`catalog.toml`. Keys of `streams` are the stream labels from the TOML.

For NERSC HPSS, `scripts/hpss_inventory.py ensembles/<name>.toml` writes
this from a metadata-only `hsi ls -lR` of each stream directory (minutes
per TB; `--listing` re-parses a saved dump offline). That script is the
worked example, not a required step: on a POSIX filesystem the same JSON
comes from `find -printf`, on an object store from a bucket listing, on a
remote site from a Globus `ls`. Only the four keys above per file are
needed.

## 3. Run the pipeline

```
module load python                       # or activate the venv; either way, where `pip install -e .` ran
E=ensembles/<name>.toml
python3 scripts/hpss_inventory.py  $E    # or write work/<name>/inventory.json yourself (section 2)
python3 scripts/build_records.py   $E    # offline; read work/<name>/records.json
python3 scripts/declare_records.py work/<name>/records.json --limit 5   # creates dataset + 5 files
python3 scripts/retrieve_records.py <name> --diff work/<name>/records.json
python3 scripts/declare_records.py work/<name>/records.json             # the rest
python3 scripts/retrieve_records.py <name> --diff work/<name>/records.json   # must print OK
```

## 4. Adding a stream later

No file editing:

```
python3 scripts/add_stream.py --ensemble <name> --stream <label> [--dir D] \
    [--parent-stream main --parent-trajectory 1000] [--note "..."] [--dry-run]
```

It refuses an unknown ensemble, an existing label (labels are permanent),
a directory another stream already uses, and an ensemble registered by
someone else unless `--force`. The stream becomes `lqcd.streams.<label>.*`
keys on the dataset record and `ensembles/<name>.toml` is regenerated; then
inventory, build, declare, diff as in section 3. `declare_records.py`
declares only records not yet in the namespace, so existing streams are
untouched, and it refreshes the dataset record's counts. A stream with a
different storage layout also needs its own `[[files]]` rule, which is a
change to the description (section 5).

## 5. Adding physics information later

No file editing; the script starts from the catalog's current state:

```
python3 scripts/update_ensemble.py --ensemble <name> --set lqcd.hmc.n_steps=16 --set observables.mpi.ncfgs=800
python3 scripts/update_ensemble.py --ensemble <name> --merge new_observables.toml   # a TOML fragment, e.g. [observables.w0]
```

`--set` paths use the description's table names (`lqcd.*`,
`observables.*`, `derived.*`, `ensemble.description`, `files` via
`--merge`); values are read as TOML. It refuses changes to streams (use
`add_stream.py`) and to the name, and an ensemble registered by someone else
unless `--force`; `--dry-run` shows the change. When the change also affects
file records (ensemble physics is copied onto every file) it says so: rebuild
and run `declare_records.py work/<name>/records.json --update-existing`,
which merges the new values onto every existing record (server-stamped
fields are untouched).

## Catalog file names

MetaCat file names must be unique within the namespace, across all
ensembles. The rule used here: the file's basename, prefixed with
`<stream dir>_` unless the basename already starts with the stream directory
name. NPLQCD configs (`cl3_..._-a_cfg_10.lime`) are unchanged; a log tarball
`log.1590.tar` that exists in several streams becomes
`cl3_32_64_b6p5_m0p1788-a_log.1590.tar`. The original basename is always kept
in `lqcd.storage.filename`. `build_records.py` aborts on any collision.
