# Looking at the catalog from a browser

Two web front ends show the LQCD metadata; both need a login.

| | URL | login |
|---|---|---|
| **MetaCat GUI** (datasets, namespaces, free-form MQL query box, find-file) | https://metacat.fnal.gov:9443/amsc_meta_prod/app/gui | FNAL Services identity, same as the token |
| **Central AmSC catalog** (OpenMetadata; renders `AmSC.common.description`) | https://openmetadata-staging.american-science-cloud.org/container/fnal-amsc-storage.fnal-amsc-data-catalog | Globus login with the AmSC session policy |

Our records appear in the central catalog under the fully qualified name
FNAL stamps on each record after ingest,
`fnal-amsc-storage.fnal-amsc-data-catalog.<dataset>."<file>"`. Only
`AmSC.common.description` is rendered there, which is why that field is
written as prose. (Source: FNAL's FDP docs,
https://github.com/FNAL-SCD/fdpdocs/blob/main/docs/metadata.md.)

## Without a login: the REST API

Every read endpoint of the MetaCat REST API answers an anonymous GET with
JSON, so a browser works as a quick inspector. Firefox renders JSON
responses as a collapsible tree; Chrome needs a JSON-viewer extension to be
readable.

Base URL: `https://metacat.fnal.gov:9443/amsc_meta_prod/app`

| what | URL |
|---|---|
| all datasets (every namespace; add `&namespace_pattern=lqcd` is not honored, filter by eye) | [`/data/datasets`](https://metacat.fnal.gov:9443/amsc_meta_prod/app/data/datasets) |
| one ensemble's dataset record (physics metadata, description) | [`/data/dataset?dataset=lqcd:cl3_32_64_b6p5_m0p1788`](https://metacat.fnal.gov:9443/amsc_meta_prod/app/data/dataset?dataset=lqcd:cl3_32_64_b6p5_m0p1788) |
| one file record | [`/data/file?did=lqcd:cl3_32_64_b6p5_m0p1788-a_cfg_10.lime&with_metadata=yes`](https://metacat.fnal.gov:9443/amsc_meta_prod/app/data/file?did=lqcd:cl3_32_64_b6p5_m0p1788-a_cfg_10.lime&with_metadata=yes) |
| any MQL query | `/data/query?with_meta=yes&query=<URL-encoded MQL>` |

Example query URL, stream a, trajectories 1000 to 1100, gauge configs only:

```
https://metacat.fnal.gov:9443/amsc_meta_prod/app/data/query?with_meta=yes&query=files%20from%20lqcd:cl3_32_64_b6p5_m0p1788%20where%20lqcd.artifact.kind%20%3D%20%22gauge_config%22%20and%20lqcd.stream%20%3D%20%22a%22%20and%20lqcd.trajectory%20%3E%3D%201000%20and%20lqcd.trajectory%20%3C%3D%201100
```

(`python3 scripts/retrieve_records.py` prints the MQL it runs on stderr; paste that into the
`query=` parameter after URL-encoding, or let `curl -G --data-urlencode` do
the encoding.)

## Browsing the files themselves

Files on the Fermi Data Platform public area are served by dCache over
WebDAV with an HTML directory listing, anonymous:

* https://amsc.fnal.gov:2880/amsc/public/lqcd/

That is where an FDP backup of configurations or propagators would appear.
Files on NERSC HPSS (everything cataloged so far) have no web listing;
their records carry a `globus://` location and the Globus web app
(https://app.globus.org/file-manager) can browse and transfer from the
`NERSC HPSS` collection after login.

## FDP's own tooling

FNAL's `fdpdocs` repo (https://github.com/FNAL-SCD/fdpdocs) has setup notes
and four example scripts: `generate_metadata.py` (scan a local directory:
name, size, sha256), `generate_metadata_remote.py` (same for files already
on FDP dCache, reading dCache's stored checksums), `declare_to_metacat.py`
and `update_metadata.py`. They are the generic, physics-free version of
`build_records.py` / `declare_records.py`; this repo exists for the HPSS
inventory, the ensemble/stream model, the `lqcd.*` schema, idempotent
batched declares and the round-trip check. `attach_checksums.py` reuses
their dCache checksum route. The intended token path is
`htgettoken -i amsc -a htvaultprod.fnal.gov` with
`METACAT_AUTH_SERVER_URL=https://metacat.fnal.gov:8143/auth/amsc`.
