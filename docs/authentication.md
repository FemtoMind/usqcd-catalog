# Authentication, for a new user

Three systems, three logins. Nothing here is specific to one person; every
command takes your own username and writes tokens under your own home.

| you want to | you need | one-time setup | each session / week |
|---|---|---|---|
| read the catalog | nothing | | |
| write to the catalog | FNAL Services account + `lqcd_write` role | request both (below) | `bin/metacat_login.sh <fnal-user>` (token lasts ~1 week) |
| move data with Globus | a Globus identity linked to each site | `globus login`, consents (below) | `globus session update <domain>` when a site's session expires |
| list / stage files on NERSC HPSS | a NERSC account in the data's project | nothing beyond NERSC login | `hsi` / `htar` pick up your NERSC credentials automatically on Perlmutter |

## 1. MetaCat (the AmSC catalog)

**Reads are anonymous.** `retrieve_records.py`, the `--diff` check, the REST
URLs in `docs/browsing.md`: no account, no token.

**Writes need an FNAL identity.** The AmSC MetaCat server accepts three
login methods and all of them resolve to an FNAL Services username:

1. Get an FNAL Services account (Fermilab's standard onboarding for
   collaborators; the AmSC team can point you to the form).
2. Ask the AmSC team (FDP contacts at FNAL) to add that username to the
   `lqcd_write` role, which owns the `lqcd` namespace. Without the role,
   `declare_files` is refused even with a valid token.
3. Install the client once (`pip install -e .` in this repo, see README).
4. Log in. The default is a password login; it prompts for your FNAL
   Services password and stores a token (about one week) in
   `~/.metacat_tokens`:

   ```
   bin/metacat_login.sh <fnal-services-username>
   ```

   Your FNAL username is usually not your NERSC/JLab/BNL login, so pass it
   explicitly, or put `export METACAT_USER=<fnal-services-username>` in your
   shell rc and omit the argument.

   The intended FNAL route is a SciToken instead of a password:

   ```
   pip install htgettoken
   export METACAT_AUTH_SERVER_URL=https://metacat.fnal.gov:8143/auth/amsc
   METACAT_AUTH_METHOD=token bin/metacat_login.sh <fnal-services-username>
   ```

   which runs `htgettoken -i amsc -a htvaultprod.fnal.gov` (browser login)
   and then `metacat auth login -m token`. Use whichever the server accepts
   on the day; the password route has been the reliable one.

5. Check state any time with `bin/check_auth.sh` (exit 0 = catalog token and
   Globus both fine; 2 = no catalog token; 3 = no Globus login; 4 = neither).

Both helpers read the server URL from `catalog.toml`, so pointing the repo
at a different MetaCat instance is a one-line change.

## 2. Globus (moving data)

Only `globus_transfer.py --submit` / `--status` and anything in
`docs/data_movement.md` need this. Catalog work does not.

1. `pip install -e '.[transfer]'` (adds `globus-cli`), then `globus login`
   (browser). Link the identities you will use: your NERSC identity for the
   NERSC collections, your FNAL identity for the FDP collections.
2. Mapped collections ask for a one-time consent the first time you touch
   them. The CLI prints the exact command; run it as printed, e.g. for
   NERSC HPSS:

   ```
   globus session consent 'urn:globus:auth:scope:transfer.api.globus.org:all[*https://auth.globus.org/scopes/9cd89cfd-6d04-11e5-ba46-22000b92c6ec/data_access]'
   ```

   The FDP LQCD collection (`0c1b254b-...`) needs the same with its own
   UUID; the FDP public collection (`73edb319-...`) needs none.
3. High-assurance collections (the NERSC DTN) also expire your identity
   session; when a transfer says "session reauthentication required", run
   `globus session update nersc.gov` and resubmit.

The collection UUIDs live in `catalog.toml` `[storage.*]` and in
`docs/storage_and_transfer.md`.

## 3. NERSC HPSS (where the NPLQCD data is)

`hpss_inventory.py`, `hpss_select.py`, `htar_members.py` and
`reduce_corrs.py` call `hsi` / `htar`. On Perlmutter those authenticate with
your NERSC login automatically. The directories under
`/home/w/wdetmold/clover-iso` are group-readable by the owning project;
you need to be a member of it to list or read them. From another site,
reach the same files through the `NERSC HPSS` Globus collection instead.

## What the scripts never do

They never prompt for a password or open a browser. Every interactive step
above is something you run yourself once; the scripts detect a missing or
expired credential, print the command to fix it, and exit. That is
deliberate so they can be driven from batch jobs and by agents.
