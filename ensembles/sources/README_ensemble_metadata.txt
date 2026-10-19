Ensemble metadata handoff — cl3 SU(3)-point (mpi/MN = 1/2) ensembles A-K
2026-10-19

CONTENTS
  ensemble_observables.csv   one row per ensemble (11 rows), columns described below.
  README_ensemble_metadata.txt  this note.

PURPOSE / PROPOSED REGISTRY SCHEMA
We would like downstream observables registered in the ensemble metadata. For
these ensembles the registered observables should be:
  1) t0    (dimensionless: t0/a^2)
  2) w0    (dimensionless: w0/a)
  3) pion mass     (lattice units: a*m_pi)
  4) nucleon mass  (lattice units: a*M_N; here the degenerate octet baryon M_B)
  5) lattice spacing
Each observable carries "central value", "statistical uncertainty", "Ncfgs",
and "notes". For the hadron masses the notes follow the style
  "<G>^3 grid of sparsened quark propagators on <N_src> timeslices with
   <N_steps> steps of wave function smearing with width <width>. Prony-Ritz
   analysis of t = [2, Lt/2] with korrfitter v0 defaults."
(the instantiated string for each ensemble is the `notes` column of the CSV).

Lattice spacing has a top-level "scheme"; each scheme has a definition,
central value, and uncertainty. Two schemes are registered:

  scheme "t0":  a = sqrt( t0^phys / (t0/a^2) )
     with sqrt(t0^phys) = 0.14474(57) fm
     [FLAG Review 2024, arXiv:2411.04268, Eq. (499), Nf = 2+1 average]

  scheme "w0":  a = w0^phys / (w0/a)
     with w0^phys = 0.17187(68) fm
     [Fermilab Lattice & MILC, Omega-baryon + gradient-flow scale setting,
      arXiv:2509.14367; full error structure 0.17187(56)(38) fm =
      (correlator fits)(other systematics)]

  Note on the choice of physical inputs: the Fermilab/MILC Omega paper quotes
  w0 only (no t0 value appears in it), so the t0 scheme uses the FLAG 2024
  Nf = 2+1 average instead. For reference, FLAG 2024 also gives
  w0 = 0.17355(92) fm (Nf = 2+1, Eq. 502) and sqrt(t0) = 0.14292(104) fm,
  w0 = 0.17256(103) fm (Nf = 2+1+1, Eqs. 498/501). Swap in whichever
  convention the registry prefers; the CSV columns make the conversion
  factors explicit.

  Scheme caveat: these ensembles sit at the SU(3)-symmetric point with
  mpi ~ 800 MeV (mpi/M_N = 1/2). Assigning a physical lattice spacing via
  flow scales measured AT THE UNPHYSICAL MASS is a convention, and the two
  schemes genuinely differ here: e.g. at beta = 6.1 the t0 scheme gives
  a ~ 0.150 fm while the w0 scheme gives a ~ 0.170 fm. The quoted "a in
  [0.077, 0.15] fm" range in our drafts corresponds to the t0 scheme.

CSV COLUMN GLOSSARY (ensemble_observables.csv)
  ensemble            label A-K (matches paper draft tables)
  beta                6/g^2, inverse bare coupling
  L, Lt               dimensionless spatial / temporal extents
  am_q                bare quark mass (3 degenerate flavors)
  c_sw                clover coefficient
  Ncfg_hadron_masses  configurations used for the hadron-mass (and BB)
                      correlator measurements; separated by 10 trajectories
                      after 500 trajectories of thermalization
  sparsening_factor   S: propagators computed on an (L/S)^3 sparse grid
  sparse_grid         (L/S)^3, written explicitly
  N_src_timeslices    number of source timeslices per configuration, equally
                      spaced in time (each holds a full (L/S)^3 source grid)
  smearing_width      gauge-invariant Gaussian wavefunction smearing width
                      (Chroma convention); same smearing applied at sink
  smearing_steps      number of smearing steps; 20 for the width-2.1
                      (beta = 6.1) ensembles, TBD for widths 4.0/6.0 (see
                      OPEN ITEMS)
  t0_over_a2 (_stat)  Wilson-flow t0/a^2 central and statistical uncertainty
  w0_over_a  (_stat)  Wilson-flow w0/a central and statistical uncertainty
  a_mpi (_stat)       pion mass in lattice units, central and statistical
  a_MN  (_stat)       octet-baryon (nucleon) mass in lattice units
  mpi_over_MN (_stat) ratio of the two
  a_fm_t0_scheme, a_fm_t0_stat, a_fm_t0_scale_err
                      lattice spacing in fm in the t0 scheme: central value,
                      statistical error propagated from t0/a^2, and the
                      (100%-correlated across ensembles) error from
                      sqrt(t0^phys)
  a_fm_w0_scheme, a_fm_w0_stat, a_fm_w0_scale_err
                      same for the w0 scheme (scale error from w0^phys)
  notes               instantiated per-ensemble analysis note in the
                      registry style described above

PROVENANCE
  - Bare parameters, Ncfg, sparsening, N_src, smearing widths: paper draft
    tables tab:ensem_details and tab:prop_details.
  - t0/a^2 and w0/a: paper draft table tab:wilson_flow (statistical errors
    only).
  - a*m_pi, a*M_N, mpi/M_N: paper draft table tab:results_single_hadron
    (hdib_single_hadron.txt), large-m median Lanczos (Prony-Ritz) energy
    estimators with bootstrap-statistical uncertainties, t in [2a, Lt/2],
    200x200 nested bootstrap. Gap/variational-bound windows are not included
    here; see the draft for those.
  - Smearing step count 20 for width 2.1: documented for the same beta = 6.1
    stream in the earlier variational paper (20 steps of gauge-invariant
    Gaussian smearing, epsilon = 0.05518 in the momentum-smearing notation,
    width parameter 2.1, with 15 steps of stout link smearing, rho = 0.1,
    on the spatial links entering the smearing kernel).

OPEN ITEMS / FLAGS
  - smearing_steps for the width-4.0 and width-6.0 ensembles (A-G) is not
    recorded in the draft or analysis files available here — fill from the
    production run scripts before registering.
  - Ensemble I N_src: the draft table says 6 source timeslices, but there is
    an unresolved editor comment questioning 6 vs 8. The CSV ships 6.
  - Ncfgs for the Wilson-flow t0/w0 measurements is not recorded here (the
    Ncfg column refers to the hadron-mass measurements); flow was measured
    on the gauge-field stream directly.
  - Whether to register lattice-spacing uncertainty as stat-only (scale error
    carried once, globally, on the scheme definition) or combined per
    ensemble is a registry design choice; both pieces are given separately.
