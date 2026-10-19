# Open questions for schema review

Things we would like opinions on from other USQCD groups before this
convention hardens. Comments as GitHub issues are easiest.

1. **Trajectory.** `lqcd.trajectory` is the integer configuration number from
   the file name (HMC update count within the stream); `lqcd.md_time` is the
   optional float `trajectory * tau0`. Groups whose file names carry MD time
   directly: is a float trajectory acceptable, or should `md_time` be the
   primary key for you?

2. **Streams.** One dataset per ensemble, `lqcd.stream` on every file, the
   unsuffixed stream labeled `main`, numbering restarting at 0 per stream. Is
   `main` the right default label? Should branch provenance
   (`lqcd.stream_parent_stream` / `_parent_trajectory`) be required when a
   stream was branched rather than started fresh?

3. **Mass vs kappa.** Both are stored for Wilson-type actions and the reader
   searches both. Is the isotropic relation kappa = 1/(2(m0+4)) enough, or do
   anisotropic groups want `lqcd.kappa.*` to be stored raw with no derived
   partner (current behavior)? Should `lqcd.mass.*` be the bare `m0` from the
   input file (current) or something else?

4. **Action vocabulary.** `lqcd.action.gauge` / `lqcd.action.fermion` values
   are free strings today (`"LW_TREE"`, `"clover"`). Do we want a short
   controlled list (`wilson`, `symanzik`, `iwasaki`, `dbw2`, `lw_tree`;
   `clover`, `wilson`, `hisq`, `dwf`, `mobius`, `overlap`, `twisted_mass`)?

5. **Supplemental files.** HMC restart inputs and log tarballs are catalogued
   as their own records (`hmc_input`, `hmc_log_archive`). Keep, or restrict the
   catalog to configurations?

6. **Ensemble-level physics on every file.** Each file record repeats the ~30
   ensemble fields so a query hit is self-describing. Any objection to the
   redundancy, versus joining against the dataset record?

7. **Names.** Catalog file names are the storage basenames (stream-prefixed on
   collision). FDP allows `/` in names, so `<ensemble>/<stream>/<basename>`
   is legal; is it preferable for groups whose basenames are not unique
   across streams or ensembles?

8. **Required fields.** Currently only `ensemble.name`. Should `lqcd.beta`,
   `lqcd.Nx..Nt` and `lqcd.action.fermion` be required too, so discovery
   queries can rely on them?

9. **Write access.** Reads are anonymous, but declaring needs an FNAL
   Services identity with the `lqcd_write` role (password, x509 or SciToken,
   all FNAL-issued). Is "build records locally, one FNAL account declares"
   an acceptable workflow for JLab/BNL groups, or do we need to ask AmSC for
   federated identities / per-group roles (`lqcd_jlab_write`, ...)?

10. **Reconciliation with the JLab catalog.** The cl21 isoClover ensembles
    are catalogued independently by JLab with their own tooling. Our records
    carry three things a reconciliation can key on: `lqcd.storage.path` (the
    NERSC HPSS path), the triple (`lqcd.ensemble`, `lqcd.stream`,
    `lqcd.trajectory`), and, once checksums exist, `checksums`. Which of
    these does the JLab side have, and should the merged record keep both
    catalogs' names as aliases (`lqcd.alias.jlab = ...`) rather than pick one?

11. **Derived artifacts.** Propagators carry
    `lqcd.parent.kind/ensemble/stream/trajectory` (+ `sha256` once we have
    checksums) pointing at the gauge config, and `lqcd.prop.*` (source type,
    smearing, flavors, masses, solver, sparsening); correlators will carry
    `lqcd.corr.*` (interpolator set, momenta, time sources). Sparse
    propagators are split into timeslice-to-all and all-to-all kinds and
    datasets (`docs/schema.md`). Suggestions welcome.

12. **Archive contents vs names.** Several cl21 propagator directories are
    only nominally organised (`sparseprops-t0/` holding archives with a
    random source timeslice per configuration; `sparseprops/<traj>.tar`
    with the timeslice nowhere in the name). We read every htar index and
    record `lqcd.prop.tsrc_list` / `flavors` / `smearing_combinations` /
    `n_members` per archive. Should a catalog record for a multi-member
    archive *require* such an index-derived summary, or is the nominal
    rule-level metadata acceptable when the index is missing?

13. **Unrecorded smearing steps.** Where no run XML survives (cl21_64_128,
    cl21_72_192, the Frontier all-to-all runs) the step count for the
    Gaussian smearing is stored as a string saying so, with the nearest
    verified production quoted. Better: a numeric field plus a separate
    `*_source = "unverified"` flag? The reader side treats a string as
    "unknown".
