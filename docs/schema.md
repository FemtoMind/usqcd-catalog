# Metadata schema for USQCD gauge ensembles in MetaCat

The AmSC catalog validates only `AmSC.common.*`. Everything under `lqcd.*` is
our convention; this document and `build_records.py` are its only enforcement.
Field names are flat dotted paths so every value is usable in MQL
(`lqcd.mass.light = -0.1788`, `lqcd.stream in ("a","b")`).

## What is required, what is optional

USQCD groups use many actions, so **no physics field is mandatory**. The
writer only insists on:

| | Required | Default if absent |
|---|---|---|
| ensemble TOML | `[ensemble].name` | |
| | `[streams]` | one stream `main` in directory `<name>` |
| | `[[files]]` rules | the NPLQCD/Chroma layout rules (`cfgs/*_cfg_N.lime`, `cfgs/*_restart_N.xml`, `log.N.tar`) |
| | `[lqcd]` | empty: records carry only identity + storage fields |
| file record | `lqcd.ensemble`, `lqcd.artifact.kind`, `lqcd.stream`, `AmSC.common.*`, `lqcd.storage.*` | |
| | `lqcd.trajectory` | absent when the matching file rule has no `trajectory` group |
| | `lqcd.md_time` | absent unless `lqcd.hmc.tau0` is given |

Everything in the physics table below is optional and action-specific; the
isotropic clover (isoclover) set is listed in full because that is what the
NPLQCD ensembles use, but a HISQ or DWF ensemble would simply use different
keys (`lqcd.action.fermion = "HISQ"`, `lqcd.mass.charm`, `lqcd.dwf.Ls`, ...).

## Mass and kappa

Either `[lqcd.mass]` or `[lqcd.kappa]` (per flavor: `light`, `strange`,
`charm`, ...) may be given. For isotropic Wilson-type actions
(`lqcd.action.fermion` in wilson/clover and not anisotropic) the writer fills in
the other via kappa = 1 / (2 (m0 + 4)) and records which was the input in
`lqcd.mass.input_convention` (`"mass"`, `"kappa"`, `"both"`). Both fields end
up on every record, so either is queryable directly.

The reader (`retrieve_records.py --mass X` or `--kappa X`) searches **both**
fields with a tolerance, so records written by a group that stored only one
representation are still found. The conversion is not applied for anisotropic
actions (where xi and nu enter) or non-Wilson actions.

## Dataset record (one per ensemble)

| Field | Value |
|---|---|
| `AmSC.common.type` | `"scientificWork"` |
| `AmSC.common.description` | Prose: action, volume, parameters, who/where/when, streams, file layout. The only field the FDP web UI shows. |
| `AmSC.common.display_name` | short label |
| `lqcd.artifact.kind` | `"gauge_ensemble"` |
| `lqcd.ensemble` | ensemble name, same as the dataset name |
| `lqcd.collaboration`, `lqcd.generated_by`, `lqcd.machine`, `lqcd.code`, `lqcd.year` | provenance |
| `lqcd.streams` | list of stream labels, e.g. `["main","a","b","c"]` |
| `lqcd.n_streams`, `lqcd.n_configs` | counts at catalog time (`n_configs` counts single-config records only) |
| `lqcd.n_config_archives` | present when configs are stored as multi-config tar archives |
| `lqcd.stream_summary` | list of strings `"<stream>: N configs, traj lo-hi"` |
| `lqcd.catalog.schema_version` | `1` |
| `lqcd.catalog.updated_at` | ISO timestamp of the last `build_records.py` |
| `lqcd.<physics>` | everything from the ensemble TOML `[lqcd]` table, flattened (below) |

## Downstream observables (dataset record only)

An ensemble TOML may carry an `[observables]` table; it is flattened onto the
dataset record as `lqcd.obs.<name>.<field>` and `lqcd.obs.list` names the
registered observables. Each observable has `quantity` (what the number is,
with its units convention), `value`, `stat` (statistical uncertainty),
`ncfgs` (configurations used, when recorded) and `notes` (free text in the
group's house style, e.g. "6^3 grid of sparsened quark propagators on 8
timeslices with 20 steps of wave function smearing with width 2.1.
Prony-Ritz analysis of t = [2, Lt/2] with korrfitter v0 defaults.").

Registered for the NPLQCD cl3 ensembles (source: `ensembles/sources/`):

| key | quantity |
|---|---|
| `lqcd.obs.t0` | `t0/a^2` (Wilson flow) |
| `lqcd.obs.w0` | `w0/a` |
| `lqcd.obs.mpi` | `a*m_pi` |
| `lqcd.obs.mN` | `a*M_N` (degenerate octet baryon at the SU(3)-symmetric point) |
| `lqcd.obs.mpi_over_mN` | `m_pi/M_N` |
| `lqcd.obs.a` | lattice spacing, see below |
| `lqcd.obs.spectrum_setup.*` | sparsening factor, sparse grid, source timeslices, smearing width/steps, Ncfgs behind the hadron masses |

**Lattice spacing** has a top-level `lqcd.obs.a.schemes` list and, per
scheme `<s>`, `lqcd.obs.a.<s>.definition` (the formula and the physical
input with its reference), `.value`, `.stat` (propagated from the flow
scale's statistical error) and `.scale_err` (from the physical input;
100% correlated across ensembles), in `lqcd.obs.a.units`. Two schemes are
registered: `t0` with sqrt(t0^phys) = 0.14474(57) fm (FLAG 2024, Nf=2+1) and
`w0` with w0^phys = 0.17187(68) fm (Fermilab/MILC, arXiv:2509.14367). The
statistical and scale errors are stored separately rather than combined;
combine them as you see fit.

Observables live on the dataset record, not on every file, because they
describe the ensemble; `retrieve_records.py --list-ensembles` shows `a`
(t0 scheme) and `a*m_pi` per ensemble, and MQL `datasets` queries can filter
on them.

## Physics fields (dataset and every file)

Taken verbatim from the Chroma HMC input. Names follow the existing
convention of the earlier NPLQCD eigenvector demo where one existed.

| Field | Type | Example (cl3_32_64_b6p5_m0p1788) |
|---|---|---|
| `lqcd.nflavors` | int | `3` |
| `lqcd.flavor_structure` | str | `"3 degenerate (m_u = m_d = m_s)"` |
| `lqcd.beta` | float | `6.5` |
| `lqcd.Nx`, `lqcd.Ny`, `lqcd.Nz`, `lqcd.Nt` | int | `32, 32, 32, 64` |
| `lqcd.volume` | int[4] | `[32,32,32,64]` (display only) |
| `lqcd.action.gauge` | str | `"LW_TREE"` (tree-level Luscher-Weisz) |
| `lqcd.action.gauge_u0` | float | `0.85703554213273` |
| `lqcd.action.fermion` | str | `"clover"` |
| `lqcd.action.fermion_state` | str | `"STOUT_FERM_STATE"` |
| `lqcd.action.csw` | float | `1.170082389372972` |
| `lqcd.action.anisotropic` | bool | `false` |
| `lqcd.action.fermion_bc` | str | `"1 1 1 -1"` |
| `lqcd.mass.light`, `lqcd.mass.strange` | float | `-0.1788` (bare m0, lattice units, Chroma `<Mass>`) |
| `lqcd.kappa.light`, `lqcd.kappa.strange` | float | `0.130848948` (derived if only mass given, or vice versa) |
| `lqcd.mass.input_convention` | str | `"mass"` |
| `lqcd.smearing.type`, `.rho`, `.n_smear` | str, float, int | `"stout", 0.125, 1` |
| `lqcd.hmc.tau0` | float | `1.414` |
| `lqcd.hmc.integrator`, `.n_steps`, `.gauge_substeps` | str, int, int | `"LCM_STS_MIN_NORM_2", 16, 5` |
| `lqcd.hmc.save_interval` | int | `10` |
| `lqcd.hmc.n_warmup_updates` | int | `100` |
| `lqcd.hmc.rational_lower_min`, `.rational_upper_max` | float | `0.003, 32` |
| `lqcd.hmc.code_version` | str | |
| `lqcd.hmc.hasenbusch_masses` | float[] | Hasenbusch preconditioner masses, cl21 ensembles (`[-0.2400, -0.2320, -0.2180, -0.1870]`) |
| `lqcd.flavor_structure` for 2+1 | str | `"2+1 (m_u = m_d < m_s)"` with `lqcd.nflavors = 3`, `lqcd.mass.light` and `lqcd.mass.strange` different |

## File record (one per stored file)

| Field | Value |
|---|---|
| `name` | catalog name, see "Names" below; unique in the namespace |
| `size` | bytes from the HPSS listing |
| `checksums` | absent (see README) |
| `AmSC.common.type` | `"artifact"` |
| `AmSC.common.description` | prose: what, which stream/trajectory, ensemble summary, where stored |
| `AmSC.common.display_name` | `"<ensemble> <stream> <kind> <trajectory>"` |
| `AmSC.common.location` | `globus://<storage collection uuid><absolute path>` |
| `AmSC.artifact.format` | `application/x-lime`, `application/xml`, `application/x-tar` |
| `lqcd.artifact.kind` | `gauge_config` (one LIME file) \| `gauge_config_archive` (a tar/htar of many configs, carries `lqcd.trajectory_min`/`_max` and, from an htar index, `lqcd.n_configs_in_archive`) \| `archive_index` (an htar `.idx`) \| `hmc_input` \| `hmc_input_archive` \| `hmc_log_archive` \| `hmc_measurement_archive` |
| `lqcd.ensemble` | ensemble name |
| `lqcd.stream` | stream label from the TOML (`main`, `a`, ...), or from a `(?P<stream>...)` group in the file rule when several streams share one directory |
| `lqcd.stream_dir` | storage directory name of the stream |
| `lqcd.stream_parent_stream`, `lqcd.stream_parent_trajectory` | only when the TOML states the branch point |
| `lqcd.trajectory` | int, the configuration number in the file name (HMC update count within the stream) |
| `lqcd.md_time` | `trajectory * lqcd.hmc.tau0` |
| `lqcd.trajectory_min`, `lqcd.trajectory_max` | for archives of many configurations: the range in the archive (from a `trajectory_min`/`trajectory_max` regex group); `lqcd.md_time_min/_max` likewise when tau0 is known |
| `lqcd.storage.system`, `.site` | `"hpss"`, `"NERSC"` |
| `lqcd.storage.path` | absolute path on that system (what `hsi get` takes) |
| `lqcd.storage.filename` | original basename (the catalog `name` may be stream-prefixed, see adding_an_ensemble.md) |
| `lqcd.storage.globus_collection` | collection UUID (same as in the location URL) |
| `lqcd.storage.mtime`, `.owner` | from the listing |
| `lqcd.<physics>` | all physics fields above, copied from the dataset |

Fields FNAL adds after ingest (`fn.locations`, `AmSC.common.fqn`, `.tags`,
`.version`) are never set here and are ignored by `retrieve_records.py --diff`.

## Names (FDP rules, enforced by build_records.py)

These come from FNAL's FDP documentation
(https://github.com/FNAL-SCD/fdpdocs/blob/main/docs/metadata.md):

* A file name must be **unique within the namespace**, across all datasets.
* Allowed characters: `A-Z a-z 0-9 - . _ /`. Slashes are legal, so
  `<stream>/<basename>` style names are an option if a group prefers them.
* A namespace whose name contains `test` is never migrated to the central
  AmSC catalog; use it for experiments only.

Our rule: the storage basename, prefixed with `<stream dir>_` when the
basename does not already start with the stream directory name (NPLQCD
configs already embed the stream; `log.1590.tar` becomes
`<ensemble>-a_log.1590.tar`). The original basename is always kept in
`lqcd.storage.filename`.

## Checksums (optional)

`checksums` is MetaCat's own per-file attribute, `{"sha256": "<hex>", ...}`.
Records are declared without it when the bytes are only on tape. Add hashes
at build time with `build_records.py --checksums hashes.json`, or later with
`attach_checksums.py`: `--from-dcache` reads the checksum FDP's dCache
computed on ingest (its REST API, as FDP's `generate_metadata_remote.py`
does) for any record whose location is on an FDP collection; `--from-json`
takes hashes computed elsewhere. Either way `lqcd.checksum.source` records
where the value came from.

## Reserved for the next artifact kinds

Propagators and correlators will reuse the ensemble/stream/trajectory keys and
add `lqcd.parent.kind = "gauge_config"`, `lqcd.parent.ensemble`,
`lqcd.parent.stream`, `lqcd.parent.trajectory` (and `lqcd.parent.sha256` once
checksums exist), plus kind-specific fields (`lqcd.prop.*`, `lqcd.corr.*`).
