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
   collision). Other groups' basenames may not be unique across streams or
   ensembles; is a `<ensemble>/<stream>/<basename>` style name preferable?

8. **Required fields.** Currently only `ensemble.name`. Should `lqcd.beta`,
   `lqcd.Nx..Nt` and `lqcd.action.fermion` be required too, so discovery
   queries can rely on them?

9. **Write access.** Reads are anonymous, but declaring needs an FNAL
   Services identity with the `lqcd_write` role (password, x509 or SciToken,
   all FNAL-issued). Is "build records locally, one FNAL account declares"
   an acceptable workflow for JLab/BNL groups, or do we need to ask AmSC for
   federated identities / per-group roles (`lqcd_jlab_write`, ...)?

10. **Derived artifacts.** For propagators and correlators the plan is
   `lqcd.parent.kind/ensemble/stream/trajectory` (+ `sha256` once we have
   checksums) pointing at the gauge config. Kind-specific fields proposed:
   `lqcd.prop.*` (source type, smearing, mass, solver tolerance, sparsening),
   `lqcd.corr.*` (interpolator set, momenta, time sources). Suggestions
   welcome before anything is written.
