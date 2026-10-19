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

1. Get an FNAL Services account and join the FDP VO. FDP's getting-started
   page (https://github.com/FNAL-SCD/fdpdocs/blob/main/docs/getting_started.md,
   updated August 2026) describes a self-service route through Fermilab's
   ServiceNow: one form to request the account or add the "Fermi Data
   Platform" affiliation, a second to request the affiliation role for your
   experiment. Approval takes hours to weeks and makes you a member of the
   `amsc` VO.
2. The `lqcd` namespace is owned by the MetaCat role `lqcd_write` (visible
   in the catalog's namespace record). Ask the FDP contacts to confirm the
   LQCD affiliation role maps onto it; without it, `declare_files` is refused
   even with a valid token.
3. Install the client once (`pip install -e .` in this repo, see README).
   metacat-client 4.1.5 (August 2026) is current.
4. Log in. The default is a password login; it prompts for your FNAL
   Services password and stores a token in `~/.metacat_tokens` (one per
   server URL; the server's default lifetime is 7 days):

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
   and then `metacat auth login -m token`. This is the only route FDP's own
   docs describe. It failed server-side for us in May 2026 (the server's
   SciToken authenticator needs a `scitokens` module that is not in
   MetaCat's requirements); whether FNAL has since installed it is not
   public, so try it, and fall back to the password route if the server
   rejects the token.

5. Check state any time with `bin/check_auth.sh` (exit 0 = catalog token and
   Globus both fine; 2 = no catalog token; 3 = no Globus login; 4 = neither).
   `metacat auth whoami` prints the expiry. To use a token on another
   machine, `metacat auth export -o <file>` there and
   `metacat auth import -i <file>` here, rather than logging in twice.

Never put the word `test` in a namespace name: FDP does not migrate such
namespaces to the central catalog.

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
`docs/storage_and_transfer.md`. FDP's access page states that Globus login
with a non-Fermilab identity gives read access to public data only; all
writes to FDP need Fermilab credentials.

## 4. The central AmSC catalog and dCache's REST API

The OpenMetadata catalog linked in `docs/browsing.md` sits behind a Globus
login with a session policy, and access has been allow-listed (DOE-lab
identities, as of mid-2026), so expect other identities to be refused. It
is read-only and nothing in this repo needs it.

dCache's REST API on port 3880, which `attach_checksums.py --from-dcache`
uses, rejects anonymous requests even under `/amsc/public`; it wants a
bearer token (`htgettoken -i amsc -a htvaultprod.fnal.gov`, add `-r <role>`
if your VO role requires it). Anonymous reads of public data work only over
WebDAV on port 2880 or xrootd.

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
