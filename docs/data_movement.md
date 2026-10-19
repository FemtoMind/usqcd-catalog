# Data movement: HPSS -> Globus -> unpack

Three scripts, one manifest, every step re-runnable:

```
hpss_select.py      hsi ls -lR + regex          -> work/<job>/manifest.json
globus_transfer.py  manifest -> Globus batch   -> task id; --verify checks the landing zone
untar_manifest.py   manifest + landed files    -> extracted trees, .extracted markers
```

The manifest is the contract: it records every source path, size, and the
regex groups (e.g. trajectory). The transfer and unpack steps only ever look
at the manifest and the destination filesystem, so a job can be restarted,
sliced, or resumed at any point without re-listing tape.

## Worked example: cl21 nucleon correlators (2026-10-16)

Goal: copy `frontier/correlators/<TRAJ>.tar` from every stream of
`cl21_48_96_b6p3_m0p2416_m0p2050` on NERSC HPSS to
`/pscratch/sd/m/mlwagman/correlators/cl21_nucleon/<stream>/` and unpack them.

### 1. Select

```
python3 scripts/hpss_select.py \
  --dirs /home/w/wdetmold/clover-iso/cl21_48_96_b6p3_m0p2416_m0p2050{,-1000,-1200,-2420,-djm-1,-djm-2,-djm-3,-djm-4} \
  --pattern '^frontier/correlators/(?P<trajectory>\d+)\.tar$' --step 20 \
  -o work/cl21_nucleon/manifest.json
```

| stream | tarballs | size | trajectories |
|---|---|---|---|
| (main) | 171 | 4.3 TB | 960..5400, 52 gaps at step 20 |
| -1000 | 146 | 3.6 TB | 500..3560, 8 gaps |
| -1200 | 155 | 3.9 TB | 500..3780, 10 gaps |
| -2420 | 139 | 3.5 TB | 500..3260 |
| -djm-1 | 116 | 2.9 TB | 500..2800 |
| -djm-2 | 85 | 2.1 TB | 500..2180 |
| -djm-3 | 137 | 3.3 TB | 500..3320, 5 gaps |
| -djm-4 | 87 | 2.2 TB | 500..2420, 10 gaps |
| total | 1036 | 25.7 TB | |

Every stream has `frontier/correlators/` and nothing but `<TRAJ>.tar` in it
(about 27 GB each).

### 2. Transfer

Plan first (writes the batch file and prints the command, submits nothing):

```
python3 scripts/globus_transfer.py work/cl21_nucleon/manifest.json \
  --src nersc_hpss --dest nersc_dtn \
  --dest-root /pscratch/sd/m/mlwagman/correlators/cl21_nucleon \
  --dest-template "{dir_name}/{basename}"
```

Then one stream at a time with `--submit` (see the quota note below):

```
python3 scripts/globus_transfer.py work/cl21_nucleon/manifest.json --src nersc_hpss --dest nersc_dtn \
  --dest-root /pscratch/sd/m/mlwagman/correlators/cl21_nucleon --dest-template "{dir_name}/{basename}" \
  --dirs cl21_48_96_b6p3_m0p2416_m0p2050-djm-2 --submit --label "cl21 nucleon djm-2"
python3 scripts/globus_transfer.py --status <task-id> --wait
python3 scripts/globus_transfer.py work/cl21_nucleon/manifest.json --dest-root ... --dest-template ... \
  --dirs cl21_48_96_b6p3_m0p2416_m0p2050-djm-2 --verify
```

One-time, interactive, by a person: the HPSS mapped collection needs
`globus session consent '<scope the CLI prints>'`, and the NERSC DTN needs
`globus session update nersc.gov` when its high-assurance session has lapsed.
`--sync-level size` means re-submitting a batch after a partial failure moves
only what is missing.

### 3. Unpack

```
sbatch bin/untar_slurm.sh work/cl21_nucleon/manifest.json \
  /pscratch/sd/m/mlwagman/correlators/cl21_nucleon "{dir_name}/{basename}" 16
```

Each tarball is extracted into its own stream directory and gets a
`<tar>.extracted` marker; `--remove-tar` (add to the sbatch line) deletes the
tarball after a successful extraction. Tarballs the transfer has not delivered
yet are reported and skipped, so the same sbatch is resubmitted after each
Globus task completes.

### Quota

`showquota` on 2026-10-16: pscratch 7.5 of 20 TiB used, so ~12.5 TiB free.
The tarballs alone are 25.7 TB and the unpacked trees add the same again
unless the tars are deleted. Either request a scratch quota increase, or run
stream by stream with `--remove-tar`, or point `--dest-root` at CFS
(`/global/cfs/cdirs/m747/...`, also served by the NERSC DTN collection).
Nothing in the scripts assumes the whole manifest fits at once.
