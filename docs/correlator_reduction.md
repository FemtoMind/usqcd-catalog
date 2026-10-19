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

Archive paths are the real NPLQCD locations on NERSC HPSS; `$SCRATCH` is
your own NERSC scratch directory.

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
python3 scripts/htar_members.py --archives /home/w/wdetmold/clover-iso/cl21_48_96_b6p3_m0p2416_m0p2050{,-1000,-1200,-2420,-djm-1,-djm-2,-djm-3,-djm-4}/frontier/correlators-NN/processed-results.tar \
    --pattern '^processed-results/all-corrs-(?P<trajectory>\d+)-(?P<tsrc>\d+)\.h5$' \
    -o work/cl21_nucleon/members.json

# 2. one xfer-queue job per stream (48 h wall clock, resumable, up to 15 running at once)
for S in cl21_48_96_b6p3_m0p2416_m0p2050{,-1000,-1200,-2420,-djm-1,-djm-2,-djm-3,-djm-4}; do
  sbatch bin/reduce_corrs_xfer.sh work/cl21_nucleon/members.json $S \
      $SCRATCH/correlators/cl21_nucleon/stage \
      $SCRATCH/correlators/cl21_nucleon/$S.h5 \
      B0_A1u_p0_f B1_G1g_p0_f B2_G1g_G1g_p0_f --by-tsrc B0_A1u_p0_f B1_G1g_p0_f
done
# (extra arguments after the dataset names go straight to reduce_corrs.py)

# 3. merge (any time; re-run when more trajectories have landed)
module load python
python3 scripts/merge_corrs.py --out $SCRATCH/correlators/cl21_nucleon/cl21_48_96_b6p3_m0p2416_m0p2050_nucleon_pion.h5 \
    $SCRATCH/correlators/cl21_nucleon/cl21_48_96_b6p3_m0p2416_m0p2050*.h5
```

Output layout: `/<stream>/trajectories`, `/<stream>/tsrc`,
`/<stream>/<dataset>` (nTRAJ, ...) averaged over the 96 sources, and
`/<stream>/by_tsrc/<dataset>` (nTRAJ, 96, ...) for the datasets named with
`--by-tsrc`, plus provenance attributes (archive path, member pattern,
datasets, dates). The datasets in these files (`all-corrs-<traj>-<tsrc>.h5`,
complex128) are

| dataset | shape per source | per configuration, source-averaged |
|---|---|---|
| `B0_A1u_p0_f` (pion, 5 x 5 momenta) | (1, 5, 1, 5, 96) | 38 KB |
| `B1_G1g_p0_f` (nucleon, 2 x 2 spin x 5 x 5 momenta) | (2, 5, 2, 5, 96) | 154 KB |
| `B2_G1g_G1g_p0_f` (NN, 4 x 172 operators squared, 48 t) | (4, 172, 4, 172, 48) | 364 MB |

so the full 1032-configuration NN set is ~375 GB per-configuration (no
binning), while keeping the per-source NN arrays would be 96 times that.
Binning over configurations is left to the analysis (merge step), where it
is cheap and reversible.

Throughput is set by tape: all eight archives are 32.7 TB and HPSS stages
them one file at a time per tape drive, so expect the full set to take a
couple of days of wall clock, most of it waiting on `hsi stage`.
