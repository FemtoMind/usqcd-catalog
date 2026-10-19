#!/bin/bash
# Index sparse-propagator htar archives (scripts/sprop_members.py) in the
# NERSC xfer queue: HPSS-capable, long wall clock, no compute charge, and it
# survives login-node session restarts. Resubmit the same command to resume.
#
#   sbatch bin/sprop_members_xfer.sh <inventory.json> '<match regex>' <out.json> [jobs]
#
# Submit from the repo root (or with --chdir=<repo root>) so the log lands in
# slurm_logs/.
#
# e.g.
#   sbatch bin/sprop_members_xfer.sh work/cl21_48_96_b6p3_m0p2416_m0p2050/inventory.json \
#       'sparseprops.*/[0-9]+\.tar$' work/cl21_48_96_b6p3_m0p2416_m0p2050_sparseprops/members.json 6
#SBATCH -q xfer
#SBATCH -t 24:00:00
#SBATCH -J sprop_members
#SBATCH -o slurm_logs/sprop_members-%j.out
#SBATCH -L scratch

set -euo pipefail
# Under sbatch, BASH_SOURCE is Slurm's spool copy of this script, so find the
# repo from the submitted script's path instead.
if [[ -n "${SLURM_JOB_ID:-}" ]]; then
    script="$(scontrol show job "$SLURM_JOB_ID" | sed -n 's/^ *Command=\([^ ]*\).*/\1/p')"
else
    script="${BASH_SOURCE[0]}"
fi
here="$(cd "$(dirname "$script")/.." && pwd)"
inv="$1"; match="$2"; out="$3"; jobs="${4:-4}"
mkdir -p "$here/slurm_logs" "$(dirname "$out")"
module load python
python3 "$here/scripts/sprop_members.py" --inventory "$inv" --match "$match" -o "$out" --jobs "$jobs" --resume
