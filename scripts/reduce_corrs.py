#!/usr/bin/env python3
"""
Pull selected correlator datasets out of htar-archived HDF5 files, one
trajectory at a time, and accumulate a per-stream database.

    reduce_corrs.py work/cl21_nucleon/members.json --stream cl21_48_96_b6p3_m0p2416_m0p2050-djm-2 \
        --datasets B0_A1m_p0_f B1_G1g_p0_f \
        --stage-dir $SCRATCH/correlators/cl21_nucleon/stage \
        --out $SCRATCH/correlators/cl21_nucleon/cl21_48_96_b6p3_m0p2416_m0p2050-djm-2.h5

For each trajectory in the members manifest (from htar_members.py):
  1. extract that trajectory's member files (all sources) from the HPSS htar
     archive into <stage-dir>/<stream>/ with bin/htar_extract.sh,
  2. open each file, read the requested datasets, average over sources
     (a running sum, so memory stays at a few files' worth even for the
     364 MB NN datasets),
  3. append to the output HDF5: the source-averaged correlator, plus the
     per-source array for datasets named with --by-tsrc, checkpointed per
     trajectory,
  4. delete the staged files.
Resumable: trajectories already in the output are skipped, so the same
command can be re-submitted after a wall-clock limit or an HPSS hiccup.

Output layout (one file per stream):
  /trajectories          int   (nTRAJ,)
  /tsrc                  int   (nSRC,)
  /<dataset>             (nTRAJ, ...)        mean over sources
  /by_tsrc/<dataset>     (nTRAJ, nSRC, ...)  only for --by-tsrc datasets (nSRC times larger)
  attrs: archive, pattern, stream, datasets, n_tsrc, created, reduce_corrs version

Needs h5py + numpy (NERSC: `module load python`). HPSS access means this must
run on a login node or in the `xfer` queue (bin/reduce_corrs_xfer.sh), not on
a compute node.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import h5py
import numpy as np

HERE = Path(__file__).resolve().parent
HTAR_EXTRACT = HERE.parent / "bin" / "htar_extract.sh"
VERSION = 1


def log(msg: str) -> None:
    sys.stderr.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    sys.stderr.flush()


def find_dataset(f: h5py.File, name: str) -> h5py.Dataset:
    """Dataset by exact path, else the unique object whose last path component is `name`."""
    if name in f and isinstance(f[name], h5py.Dataset):
        return f[name]
    hits = []
    f.visititems(lambda p, o: hits.append(p) if isinstance(o, h5py.Dataset) and p.rsplit("/", 1)[-1] == name else None)
    if len(hits) != 1:
        raise KeyError(f"{name}: {len(hits)} matches in {f.filename}: {hits[:5]}")
    return f[hits[0]]


def read_one(path: str, datasets: list[str]) -> dict[str, np.ndarray]:
    with h5py.File(path, "r") as f:
        return {d: np.asarray(find_dataset(f, d)[()]) for d in datasets}


def done_trajectories(out: Path) -> set[int]:
    if not out.exists():
        return set()
    with h5py.File(out, "r") as f:
        return set(int(t) for t in f["trajectories"][()]) if "trajectories" in f else set()


def append(out: Path, traj: int, tsrc: list[int], means: dict[str, np.ndarray],
           by_src: dict[str, np.ndarray], meta: dict) -> None:
    """means[name] is the source average; by_src[name] (nSRC, ...) only for --by-tsrc datasets.
    Resizable datasets are created on first use."""
    with h5py.File(out, "a") as f:
        if "trajectories" not in f:
            f.create_dataset("trajectories", shape=(0,), maxshape=(None,), dtype="i8")
            f.create_dataset("tsrc", data=np.asarray(tsrc, dtype="i8"))
            for k, v in meta.items():
                f.attrs[k] = v
            f.attrs["created"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        elif not np.array_equal(f["tsrc"][()], np.asarray(tsrc)):
            raise RuntimeError(f"trajectory {traj}: source list {tsrc} differs from the file's {f['tsrc'][()].tolist()}")
        n = f["trajectories"].shape[0]
        for name, mean in means.items():
            if name not in f:
                f.create_dataset(name, shape=(0,) + mean.shape, maxshape=(None,) + mean.shape, dtype=mean.dtype,
                                 chunks=(1,) + mean.shape)
            f[name].resize(n + 1, axis=0)
            f[name][n] = mean
        for name, arr in by_src.items():
            key = f"by_tsrc/{name}"
            if key not in f:
                f.create_dataset(key, shape=(0,) + arr.shape, maxshape=(None,) + arr.shape,
                                 dtype=arr.dtype, chunks=(1,) + arr.shape)
            f[key].resize(n + 1, axis=0)
            f[key][n] = arr
        f["trajectories"].resize(n + 1, axis=0)
        f["trajectories"][n] = traj
        f.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("members_json")
    ap.add_argument("--stream", required=True, help="stream name as in the members manifest")
    ap.add_argument("--datasets", nargs="+", required=True)
    ap.add_argument("--by-tsrc", nargs="*", default=[], metavar="DATASET",
                    help="also keep the per-source array for these datasets (nSRC times the storage; "
                         "fine for the KB-sized pion/nucleon, not for the 364 MB NN blocks)")
    ap.add_argument("--stage-dir", required=True)
    ap.add_argument("--out", required=True, help="per-stream output HDF5")
    ap.add_argument("--trajectory-min", type=int)
    ap.add_argument("--trajectory-max", type=int)
    ap.add_argument("--limit", type=int, help="stop after N new trajectories (smoke test)")
    ap.add_argument("--jobs", type=int, default=8, help="parallel HDF5 readers")
    ap.add_argument("--keep-staged", action="store_true")
    ap.add_argument("--no-prestage", action="store_true",
                    help="skip `hsi stage -w <archive>`; by default the whole archive is brought from tape to the "
                         "HPSS disk cache first, because htar cannot read a member until the file is on disk")
    ap.add_argument("--dry-run", action="store_true", help="list what would be done, touch nothing")
    args = ap.parse_args()

    with open(args.members_json) as fh:
        members = json.load(fh)
    arc = next((a for a, v in members["archives"].items() if v["stream"] == args.stream), None)
    if arc is None:
        sys.exit(f"stream {args.stream} not in {args.members_json}; have {[v['stream'] for v in members['archives'].values()]}")
    info = members["archives"][arc]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(args.stage_dir) / args.stream
    stage.mkdir(parents=True, exist_ok=True)

    have = done_trajectories(out)
    trajs = sorted(int(t) for t in info["trajectories"])
    trajs = [t for t in trajs if (args.trajectory_min is None or t >= args.trajectory_min)
             and (args.trajectory_max is None or t <= args.trajectory_max)]
    todo = [t for t in trajs if t not in have]
    if args.limit:
        todo = todo[: args.limit]
    log(f"{args.stream}: {len(trajs)} trajectories selected, {len(have)} already in {out}, {len(todo)} to do")
    if args.dry_run:
        for t in todo[:10]:
            print(t, info["trajectories"][str(t)]["n_tsrc"], "sources")
        return 0

    if todo and not args.no_prestage:
        # HPSS stages a whole file to disk before any byte of it can be read, so
        # ask for it explicitly and wait; subsequent per-trajectory htar reads
        # then come from the disk cache instead of each re-queuing a tape mount.
        t0 = time.time()
        log(f"hsi stage -w {arc} ({info['total_bytes'] / 1e12:.1f} TB archive; this is the tape wait)")
        r = subprocess.run(["hsi", "-q", "stage", "-w", arc], capture_output=True, text=True)
        if r.returncode != 0:
            log(f"hsi stage failed (exit {r.returncode}): {(r.stdout + r.stderr)[-800:]}")
            return 2
        log(f"archive on disk after {(time.time() - t0) / 60:.0f} min")

    keep_src = set(args.by_tsrc)
    unknown = keep_src - set(args.datasets)
    if unknown:
        sys.exit(f"--by-tsrc names not in --datasets: {sorted(unknown)}")
    meta = {"archive": arc, "pattern": members["pattern"], "stream": args.stream,
            "datasets": json.dumps(args.datasets), "by_tsrc": json.dumps(sorted(keep_src)),
            "reduce_corrs_version": VERSION}
    for i, t in enumerate(todo, 1):
        tinfo = info["trajectories"][str(t)]
        mem = tinfo["members"]
        t0 = time.time()
        listfile = stage / f"members_{t}.lst"
        listfile.write_text("".join(m["path"] + "\n" for m in mem))
        # htar's open can time out with "error -5" while HPSS is still staging
        # the archive (seen 2026-10-16), so re-stage and retry a few times.
        for attempt in range(1, 4):
            r = subprocess.run(["bash", str(HTAR_EXTRACT), arc, str(stage), "-L", str(listfile)], capture_output=True, text=True)
            if r.returncode == 0:
                break
            log(f"traj {t}: htar extract failed (exit {r.returncode}, attempt {attempt}/3): "
                f"{(r.stdout + r.stderr).strip()[-400:]}")
            if attempt < 3:
                subprocess.run(["hsi", "-q", "stage", "-w", arc], capture_output=True, text=True)
        else:
            return 2
        paths = [str(stage / m["path"]) for m in mem]
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            log(f"traj {t}: {len(missing)} members not extracted, e.g. {missing[0]}")
            return 2
        t1 = time.time()
        # Running sum over sources as the files arrive (in order), so memory is
        # bounded by the reader count, not by nSRC copies of each dataset.
        sums: dict[str, np.ndarray] = {}
        src_arrays: dict[str, list[np.ndarray]] = {d: [] for d in keep_src}
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            for one in ex.map(lambda p: read_one(p, args.datasets), paths):
                for d, a in one.items():
                    sums[d] = a.astype(a.dtype, copy=True) if d not in sums else sums[d] + a
                    if d in keep_src:
                        src_arrays[d].append(a)
        means = {d: sums[d] / len(paths) for d in args.datasets}
        by_src = {d: np.stack(v) for d, v in src_arrays.items()}
        shapes = {d: (len(paths),) + means[d].shape for d in args.datasets}
        tsrc = [m.get("tsrc", k) for k, m in enumerate(mem)]
        meta["n_tsrc"] = len(tsrc)
        append(out, t, tsrc, means, by_src, meta)
        if not args.keep_staged:
            for p in paths:
                os.remove(p)
            listfile.unlink()
        log(f"traj {t} ({i}/{len(todo)}): extract {t1 - t0:.0f}s, reduce {time.time() - t1:.0f}s, "
            f"{len(mem)} sources, shapes {shapes}")
    # clean empty staged tree
    if not args.keep_staged:
        shutil.rmtree(stage / "processed-results", ignore_errors=True)
    log(f"done: {out} now holds {len(done_trajectories(out))} trajectories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
