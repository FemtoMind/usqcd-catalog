#!/usr/bin/env bash
# Report MetaCat token and Globus login state for the catalog in catalog.toml.
#
#   bin/check_auth.sh
#
# Exit codes (so scripts and agents can branch on them):
#   0  MetaCat token valid and Globus logged in
#   2  MetaCat token missing/expired  (catalog reads still work; declaring does not)
#   3  Globus not logged in           (metadata steps fine; transfers blocked)
#   4  both

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cfg="${CATALOG_TOML:-$here/catalog.toml}"
# Pull the two values out of catalog.toml with sed so this works with any
# system python (tomllib needs 3.11; the login-node python3 may be older).
PY="$(sed -n 's/^python *= *"\(.*\)".*/\1/p' "$cfg" | head -1)"
PY="${PY:-python3}"                      # [venv].python is optional; default: python3 on PATH
export METACAT_SERVER_URL="$(sed -n 's/^server_url *= *"\(.*\)".*/\1/p' "$cfg" | head -1)"
# The `metacat` CLI is a console script of metacat-client; invoking it through
# the interpreter works whether it was installed into a venv or with --user.
mc() { "$PY" -c 'import sys; from metacat.ui.metacat_ui import main; sys.argv[0] = "metacat"; main()' "$@"; }
gl_bin="$(command -v globus || echo "$(dirname "$(command -v "$PY")")/globus")"

mc_ok=0; gl_ok=0
echo "--- MetaCat ($METACAT_SERVER_URL) via $PY"
if mc auth whoami 2>&1; then mc_ok=1; else
  echo "no valid token: reads work anonymously; to declare run  bin/metacat_login.sh"
fi
echo "--- Globus"
if [ -x "$gl_bin" ] && "$gl_bin" whoami 2>&1; then gl_ok=1; else
  echo "not logged in (only matters for transfers): globus login   (pip install globus-cli if missing)"
fi
[ $mc_ok -eq 1 ] && [ $gl_ok -eq 1 ] && exit 0
[ $mc_ok -eq 0 ] && [ $gl_ok -eq 0 ] && exit 4
[ $mc_ok -eq 0 ] && exit 2
exit 3
