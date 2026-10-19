#!/bin/bash
# Extract selected members of an htar archive on HPSS into a directory.
#
#   bin/htar_extract.sh <hpss archive> <dest dir> <member path> [<member path> ...]
#   bin/htar_extract.sh <hpss archive> <dest dir> -L <file with one member path per line>
#
# htar reads only the requested members (it seeks using the .idx), so pulling a
# handful of files out of a multi-TB archive does not stage the whole archive.
# Members land under <dest dir> with their archive-relative paths. Exit status
# is htar's.
set -euo pipefail
archive="$1"; dest="$2"; shift 2
mkdir -p "$dest"
cd "$dest"
if [ "${1:-}" = "-L" ]; then
    exec htar -xf "$archive" -L "$2"
fi
exec htar -xf "$archive" "$@"
