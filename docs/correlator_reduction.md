# Reducing archived correlators without copying the archives

Problem: the correlators we want are a few small datasets inside ~100k HDF5
files that live in multi-TB htar archives on tape. Copying and unpacking the
archives (docs/data_movement.md) would cost tens of TB of scratch for a few
MB of results. Instead, read them where they are.

```
htar_members.py   htar -tvf (index only)  -> work/<job>/members.json   (trajectory -> source files)
reduce_corrs.py   per trajectory: htar extract sources -> read datasets -> append to <stream>.h5 -> delete
merge_corrs.py    <stream>.h5 ... -> one database, a group per stream
```

Two facts about HPSS shape the design:

* **A file must be fully on the disk cache before htar can read any member.**
  A 3 to 6 TB archive takes hours to come off tape. `reduce_corrs.py` issues
  `hsi stage -w <archive>` up front so the wait happens once per stream, then
  every per-trajectory extract is a disk read.
* **htar extracts members by offset**, so pulling one trajectory's 96 source
  files (35 GB) out of a 5 TB archive costs 35 GB of I/O, not 5 TB.

Scratch use is bounded by one trajectory in flight per job (35 GB), and the
output is small: per stream a few MB.

## Worked example: cl21 nucleon / pion correlators (2026-10-16)

Archives: `<stream>/frontier/correlators-NN/processed-results.tar` for the
eight streams of `cl21_48_96_b6p3_m0p2416_m0p2050`. Members
`processed-results/all-corrs-<TRAJ>-<TSRC>.h5`, 364 MB each.

| stream | trajectories | sources/traj | files | archive |
|---|---|---|---|---|
| main | 171 (960..5400) | 96 | 16416 | 5.4 TB |
| -1000 | 146 (500..3560) | 96 | 14016 | 4.6 TB |
| -1200 | 155 (500..3780) | 96 | 14880 | 4.9 TB |
| -2420 | 139 (500..3260) | 96 | 13344 | 4.4 TB |
| -djm-1 | 116 (500..2800) | 96 | 11136 | 3.7 TB |
| -djm-2 | 85 (500..2180) | 96 | 8160 | 2.7 TB |
| -djm-3 | 133 (500..3320) | 96 | 12768 | 4.2 TB |
| -djm-4 | 87 (500..2420) | 96 | 8352 | 2.8 TB |

Datasets wanted: `B0_A1m_p0_f` (pion) and `B1_G1g_p0_f` (nucleon), averaged
over the 96 sources per trajectory; the per-source arrays are kept too since
they are tiny.

```
# 1. index (seconds; reads only the .idx files)
scripts/htar_members.py --archives /home/w/wdetmold/clover-iso/cl21_48_96_b6p3_m0p2416_m0p2050{,-1000,-1200,-2420,-djm-1,-djm-2,-djm-3,-djm-4}/frontier/correlators-NN/processed-results.tar \
    --pattern '^processed-results/all-corrs-(?P<trajectory>\d+)-(?P<tsrc>\d+)\.h5$' \
    -o work/cl21_nucleon/members.json

# 2. one xfer-queue job per stream (48 h wall clock, resumable, up to 15 running at once)
for S in cl21_48_96_b6p3_m0p2416_m0p2050{,-1000,-1200,-2420,-djm-1,-djm-2,-djm-3,-djm-4}; do
  sbatch bin/reduce_corrs_xfer.sh work/cl21_nucleon/members.json $S \
      /pscratch/sd/m/mlwagman/correlators/cl21_nucleon/stage \
      /pscratch/sd/m/mlwagman/correlators/cl21_nucleon/$S.h5 B0_A1m_p0_f B1_G1g_p0_f
done

# 3. merge (any time; re-run when more trajectories have landed)
module load python
scripts/merge_corrs.py --out /pscratch/sd/m/mlwagman/correlators/cl21_nucleon/cl21_48_96_b6p3_m0p2416_m0p2050_nucleon_pion.h5 \
    /pscratch/sd/m/mlwagman/correlators/cl21_nucleon/cl21_48_96_b6p3_m0p2416_m0p2050*.h5
```

Output layout: `/<stream>/trajectories`, `/<stream>/tsrc`,
`/<stream>/B0_A1m_p0_f` (nTRAJ, ...), `/<stream>/B1_G1g_p0_f`,
`/<stream>/by_tsrc/<dataset>` (nTRAJ, 96, ...), plus provenance attributes
(archive path, member pattern, datasets, dates).

Throughput is set by tape: all eight archives are 32.7 TB and HPSS stages
them one file at a time per tape drive, so expect the full set to take a
couple of days of wall clock, most of it waiting on `hsi stage`.
