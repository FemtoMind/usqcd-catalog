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
export METACAT_SERVER_URL="$(sed -n 's/^server_url *= *"\(.*\)".*/\1/p' "$cfg" | head -1)"
venv_bin="$(dirname "${PY:-$(command -v python3)}")"

mc=0; gl=0
echo "--- MetaCat ($METACAT_SERVER_URL)"
if "$venv_bin/metacat" auth whoami 2>&1; then mc=1; else
  echo "no valid token: reads work anonymously; to declare run  bin/metacat_login.sh"
fi
echo "--- Globus"
if [ -x "$venv_bin/globus" ] && "$venv_bin/globus" whoami 2>&1; then gl=1; else
  echo "not logged in (only matters for transfers): $venv_bin/globus login"
fi
[ $mc -eq 1 ] && [ $gl -eq 1 ] && exit 0
[ $mc -eq 0 ] && [ $gl -eq 0 ] && exit 4
[ $mc -eq 0 ] && exit 2
exit 3
