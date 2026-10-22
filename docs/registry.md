# The ensemble registry, and how several groups share it without collisions

The catalog is the registry. Each ensemble is one dataset `lqcd:<ensemble>`
whose record carries the full description: physics, observables, file rules,
provenance, and one set of keys per Markov chain stream. Derived datasets
(`lqcd:<ensemble>_<product>`) work the same way and point back at their
parent. The TOML files in `ensembles/` and `derived_datasets/` are snapshots
generated from the catalog, kept in git for history, review and recovery.

Write access to the registry is the MetaCat `lqcd_write` role, not write
access to this repository. A group that cannot push here can still register
an ensemble or a stream; the snapshot reaches the repository at the next
sync.

## The scripts

| task | command | edits a file? |
|---|---|---|
| see what is registered | `retrieve_records.py --summary` | no |
| new ensemble or derived dataset | `register_ensemble.py my.toml` (start from `templates/`) | once, by hand |
| new stream of an ensemble | `add_stream.py --ensemble X --stream Y [--dir D]` | no |
| change physics, observables, text | `sync_registry.py --ensemble X`, edit `ensembles/X.toml`, `update_ensemble.py ensembles/X.toml` | the snapshot |
| catalog files | inventory, `build_records.py`, `declare_records.py`, `retrieve_records.py --diff` | no |
| refresh local snapshots | `sync_registry.py` (all) or `--ensemble X` | regenerated |

Every writing script syncs the snapshot of the dataset it touches from the
catalog before it checks anything and again after it writes, so a local
checkout is current after every change. Commit the regenerated snapshot when
convenient; nothing breaks if you don't.

## The rules

1. **Names are permanent and unique.** An ensemble name or a stream label
   that already exists is refused, with who registered it and when; pick a
   different one. Names are never renamed, because every file record and
   every derived dataset refers to them. A stream that is abandoned stays
   registered (say so in a `--note`).
2. **Other groups' entries need their agreement.** Writing to an ensemble,
   or to a stream, registered by another account is refused unless `--force`;
   the message names the account. `--force` is for when you have agreed it,
   not for getting past the message.
3. **Write from a current copy.** Each registry entry has a revision
   (`lqcd.catalog.revision`, a time and the writer's account) that changes
   whenever the entry changes. Your snapshot records the revision it was
   synced at (`[catalog] revision`). If someone else changed the entry since,
   `declare_records.py` and `update_ensemble.py` refuse and tell you to sync,
   rebuild and re-run. This is what stops two groups from silently
   overwriting each other.
4. **Streams are separate keys.** Each stream lives in its own keys
   (`lqcd.streams.<label>.dir`, `.parent_stream`, `.parent_trajectory`,
   `.registered_by`, `.registered_at`), so two people adding different
   streams at the same moment write different keys and both survive.
   `lqcd.stream_labels` is merged, never shrunk; `add_stream.py` re-reads it
   after writing and repairs it if a concurrent writer dropped its label.
5. **Newer schemas are not overwritten by older scripts.** A record whose
   `lqcd.catalog.schema_version` is newer than your checkout knows is
   refused; `git pull` first.
6. **Snapshots are never merged by hand.** If git reports a conflict in
   `ensembles/` or `derived_datasets/`, run `python3 scripts/sync_registry.py`
   and commit the result: the catalog wins. The output is deterministic, so
   two people syncing the same catalog state produce identical files.
   `sync_registry.py --check` exits 1 when a snapshot is stale (suitable for
   CI).

## Naming

* Ensembles: `<action family>_<L>_<T>_b<beta>_m<mass>[_m<mass>...]` with `p`
  for the decimal point (`cl21_48_96_b6p3_m0p2416_m0p2050`), as in the
  existing entries; letters, digits, `.`, `-`, `_` only. Prefix with a
  group tag if your family name could clash with another group's.
* Streams: `main` for the original chain; for a branch, the trajectory it
  was branched at (`1000`) or a short tag (`a`, `djm-1`); letters, digits,
  `-`, `_` only.
* Derived datasets: `<ensemble>_<product>` (`_sparseprops`,
  `_sparseprops_a2a`, ...).

## What is not covered yet

Copies of the same files at another site (an FDP backup, say) are a new
location of the same records, not a new stream; per-file replica lists come
with the first FDP backup. MetaCat has no per-dataset permissions, so rules
1 and 2 are enforced by these scripts, not by the server; someone writing
with other tools can bypass them, which `sync_registry.py --check` and the
git history of the snapshots will show.
