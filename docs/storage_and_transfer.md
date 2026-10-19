# Storage systems and moving data

The catalog records *where the bytes are*; it does not hold them. Every file
record carries

* `AmSC.common.location = globus://<collection-uuid>/<absolute path>`
* `lqcd.storage.system`, `.site`, `.path`, `.globus_collection`

so a consumer at another lab can hand the location straight to Globus
without knowing the source site's local tools (`hsi` at NERSC).

## Collections in `catalog.toml`

| key | collection | notes |
|---|---|---|
| `nersc_hpss` | NERSC HPSS `9cd89cfd-6d04-11e5-ba46-22000b92c6ec` | tape; where the NPLQCD clover-iso ensembles live. Mapped collection: needs `globus session consent` with its `data_access` scope before `globus ls`/transfer. Retrieval from tape is slow and should be batched by tape order. |
| `nersc_dtn` | NERSC DTN `9d6d994a-6d04-11e5-ba46-22000b92c6ec` | pscratch / CFS. High assurance: `globus session update nersc.gov` periodically. |
| `fdp_lqcd` | Fermi Data Platform LQCD `0c1b254b-e32d-409c-bfe3-4a81fef25892` | dCache at FNAL under `/amsc/lqcd`. Intended backup target for configurations and sparse propagators. Needs data_access consent and session reauth. |
| `fdp_public` | Fermi Data Platform Public Data `73edb319-d876-4143-b5fc-5a47c61b0c02` | guest collection, anonymous reads, `/amsc/public/lqcd`. |
| `bnl_sdcc` | BNL SDCC `12782fb1-a599-4f18-b0fb-2e849681e214` | |
| `jlab_scidtn1` | JLab SCIDTN1 `81b20848-b468-11e9-98d4-0a63aa6b37da` | unverified |
| `fnal_lq2` | Fermilab LQ2 `1bfdb131-876d-482b-94a2-ed9e1b142791` | |

## Getting files

1. Find them (anonymous):

   ```
   python3 scripts/retrieve_records.py cl3_32_64_b6p5_m0p1788 --kind gauge_config --stream a \
       --trajectory-min 1000 --trajectory-max 1200 --paths > paths.txt
   ```

2. At NERSC, `hsi get` the paths; from elsewhere, build a Globus batch file
   from the `globus://` locations and run

   ```
   globus transfer <src-uuid> <dest-uuid> --batch batch.txt --sync-level checksum
   ```

   The first transfer from a mapped collection prints the exact
   `globus session consent '...'` command to run; it is an interactive
   browser flow.

## Backing up to FDP (planned)

Same records, one more location. The plan is a `transfer_records.py` step
that takes a `retrieve_records.py` dump, submits one Globus task from the
record's current collection to `fdp_lqcd`, and on success appends the new
`globus://0c1b254b-.../amsc/lqcd/...` URL to the record (FNAL's ingest adds
`fn.locations` with https/xrootd/globus forms of the same path).

## Checksums

HPSS has no stored hashes for these files (`hsi hashlist` reports none) and
the generation-time copies on scratch are purged, so every record is declared
without `checksums`. The cheap route is the FDP backup itself: dCache
computes a checksum for every file it ingests and serves it from its REST
API (`https://amsc.fnal.gov:3880/api/v1/namespace/<path>?checksum=true`,
bearer token required). That is exactly what FDP's
`examples/generate_metadata_remote.py` reads, and
`scripts/attach_checksums.py --from-dcache` does the same for records already
in our catalog:

```
python3 scripts/attach_checksums.py cl3_32_64_b6p5_m0p1788 --from-dcache           # report to work/<ens>/checksums.json
python3 scripts/attach_checksums.py cl3_32_64_b6p5_m0p1788 --from-dcache --apply   # update_file() on each record
```

It finds the dCache path from each record's locations (an `https://` URL on
a storage entry's `https_base`, or a `globus://` URL on an FDP collection in
`catalog.toml`), so it only has something to do for files that have been
copied to FDP. Hashes computed anywhere else go in with
`--from-json hashes.json`, or at build time with
`build_records.py --checksums hashes.json`.

Note that dCache's default algorithm is adler32; ask for sha256 at transfer
time (`globus transfer --sync-level checksum` verifies but does not publish)
or compute it on a DTN if you need a cryptographic hash. The catalog stores
whatever algorithms it is given, keyed by name.
