#!/usr/bin/env bash
# Get a MetaCat token for the catalog named in catalog.toml.
#
#   bin/metacat_login.sh [FNAL-services-username]
#
# Interactive (prompts for your FNAL Services password), so run it yourself in
# a terminal; an agent or batch job cannot. The token lands in ~/.metacat_tokens
# and is valid for about a week; every script in scripts/ picks it up from
# there. Catalog *reads* never need it.
#
# Username precedence: argument, $METACAT_USER, then your login name. Your
# FNAL Services username may differ from your local one (NERSC mlwagman is
# FNAL mwagman), so set METACAT_USER in your shell rc if they differ.
#
# Token-based login (htgettoken) is the intended FNAL path but was broken
# server-side when this was written (missing scitokens module); set
# METACAT_AUTH_METHOD=token to try it.

set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cfg="${CATALOG_TOML:-$here/catalog.toml}"

# Pull the two values out of catalog.toml with sed so this works with any
# system python (tomllib needs 3.11; the login-node python3 may be older).
PY="$(sed -n 's/^python *= *"\(.*\)".*/\1/p' "$cfg" | head -1)"
PY="${PY:-python3}"                      # [venv].python is optional; default: python3 on PATH
export METACAT_SERVER_URL="$(sed -n 's/^server_url *= *"\(.*\)".*/\1/p' "$cfg" | head -1)"
# Run the metacat CLI through the interpreter so a venv or --user install both work.
mc() { "$PY" -c 'import sys; from metacat.ui.metacat_ui import main; sys.argv[0] = "metacat"; main()' "$@"; }

USER_NAME="${1:-${METACAT_USER:-$USER}}"
METHOD="${METACAT_AUTH_METHOD:-password}"

echo "MetaCat server: $METACAT_SERVER_URL  (client: $PY)"
case "$METHOD" in
  password)
    echo ">>> metacat auth login -m password $USER_NAME   (FNAL Services password)"
    mc auth login -m password "$USER_NAME"
    ;;
  token)
    echo ">>> htgettoken -i amsc -a htvaultprod.fnal.gov"
    htgettoken -i amsc -a htvaultprod.fnal.gov
    mc auth login -m token "$USER_NAME"
    ;;
  *) echo "METACAT_AUTH_METHOD must be password or token" >&2; exit 2 ;;
esac
mc auth whoami
