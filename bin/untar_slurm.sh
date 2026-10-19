#!/bin/bash
# Unpack a manifest's tarballs on a Perlmutter CPU node (not a login node).
#
#   sbatch [-A <account>] bin/untar_slurm.sh <manifest.json> <dest-root> [dest-template] [jobs]
#   (no account is hard-coded; pass -A or rely on your Slurm default)
#
# e.g.
#   sbatch bin/untar_slurm.sh work/cl21_nucleon/manifest.json \
#       $SCRATCH/correlators/cl21_nucleon "{dir_name}/{basename}" 16
#
# Re-runnable: untar_manifest.py skips tarballs with an .extracted marker and
# ignores ones the transfer has not delivered yet, so submit this again after
# each Globus task finishes.
#SBATCH -C cpu
#SBATCH -q regular
#SBATCH -N 1
#SBATCH -t 06:00:00
#SBATCH -J untar_manifest
#SBATCH -o slurm_logs/untar-%j.out

set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$(sed -n 's/^python *= *"\(.*\)".*/\1/p' "$here/catalog.toml" | head -1)"
command -v module >/dev/null 2>&1 && module load python   # NERSC: python3 with tomllib
mkdir -p "$here/slurm_logs"
"${PY:-python3}" "$here/scripts/untar_manifest.py" "$1" --dest-root "$2" \
    --dest-template "${3:-{dir_name}/{rel_path}}" --jobs "${4:-16}"
