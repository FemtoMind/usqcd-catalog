# Adding an ensemble

Everything an ensemble needs is one TOML file in `ensembles/`. The scripts
never need editing for a new ensemble; if they do, that is a bug in the
template, please report it.

## 1. Write `ensembles/<name>.toml`

Copy `ensembles/cl3_32_64_b6p5_m0p1788.toml` and edit. The file has four
tables; only the first is required.

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

## 2. Run the pipeline

```
module load python                       # or activate the venv; either way, where `pip install -e .` ran
E=ensembles/<name>.toml
python3 scripts/hpss_inventory.py  $E    # metadata-only `hsi ls -lR`, minutes per TB
python3 scripts/build_records.py   $E    # offline; read work/<name>/records.json
python3 scripts/declare_records.py work/<name>/records.json --limit 5   # creates dataset + 5 files
python3 scripts/retrieve_records.py <name> --diff work/<name>/records.json
python3 scripts/declare_records.py work/<name>/records.json             # the rest
python3 scripts/retrieve_records.py <name> --diff work/<name>/records.json   # must print OK
```

## 3. Changing metadata later

Edit the TOML, rebuild, then

```
python3 scripts/declare_records.py work/<name>/records.json --update-existing
```

which merges the new values onto every existing record (server-stamped
fields are untouched) and re-runs the diff.

## Catalog file names

MetaCat file names must be unique within the namespace, across all
ensembles. The rule used here: the file's basename, prefixed with
`<stream dir>_` unless the basename already starts with the stream directory
name. NPLQCD configs (`cl3_..._-a_cfg_10.lime`) are unchanged; a log tarball
`log.1590.tar` that exists in several streams becomes
`cl3_32_64_b6p5_m0p1788-a_log.1590.tar`. The original basename is always kept
in `lqcd.storage.filename`. `build_records.py` aborts on any collision.
