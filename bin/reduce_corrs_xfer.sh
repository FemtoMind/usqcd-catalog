#!/bin/bash
# Run reduce_corrs.py for one stream in the NERSC xfer queue (HPSS-capable,
# long wall clock, no compute charge). One job per stream; resubmit the same
# command if it hits the wall clock, it resumes where it stopped.
#
#   sbatch bin/reduce_corrs_xfer.sh <members.json> <stream> <stage-dir> <out.h5> <dataset> [<dataset> ...]
#
# e.g.
#   sbatch bin/reduce_corrs_xfer.sh work/cl21_nucleon/members.json \
#       cl21_48_96_b6p3_m0p2416_m0p2050-djm-2 \
#       /pscratch/sd/m/mlwagman/correlators/cl21_nucleon/stage \
#       /pscratch/sd/m/mlwagman/correlators/cl21_nucleon/cl21_48_96_b6p3_m0p2416_m0p2050-djm-2.h5 \
#       B0_A1m_p0_f B1_G1g_p0_f
#SBATCH -q xfer
#SBATCH -t 48:00:00
#SBATCH -J reduce_corrs
#SBATCH -o slurm_logs/reduce-%j.out
#SBATCH -L scratch

set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
members="$1"; stream="$2"; stage="$3"; out="$4"; shift 4
mkdir -p "$here/slurm_logs"
module load python          # NERSC python has h5py + numpy
python3 "$here/scripts/reduce_corrs.py" "$members" --stream "$stream" --stage-dir "$stage" --out "$out" \
    --datasets "$@" --jobs 8
