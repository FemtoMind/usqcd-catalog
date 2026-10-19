#!/usr/bin/env python3
"""
Merge per-stream reduce_corrs.py outputs into one database.

    merge_corrs.py --out /pscratch/.../cl21_48_96_b6p3_m0p2416_m0p2050_nucleon_pion.h5 \
        /pscratch/.../cl21_48_96_b6p3_m0p2416_m0p2050.h5 /pscratch/.../cl21_..._-djm-2.h5 ...

Layout of the merged file:
  /<stream>/trajectories, /<stream>/tsrc, /<stream>/<dataset>, /<stream>/by_tsrc/<dataset>
      exact copies of each per-stream file (its attrs become the group's attrs)
  /streams               list of stream names
  attrs: datasets, merged_at, sources (the input file names)

Re-running overwrites the output. Safe to run while reduce jobs are still
appending to the inputs: it copies whatever trajectories exist at that moment
and records the count per stream in /<stream>.attrs["n_trajectories"].
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import h5py


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="per-stream HDF5 files from reduce_corrs.py")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    streams = []
    datasets = None
    with h5py.File(args.out, "w") as out:
        for p in args.inputs:
            with h5py.File(p, "r") as src:
                stream = src.attrs.get("stream", Path(p).stem)
                if isinstance(stream, bytes):
                    stream = stream.decode()
                g = out.create_group(stream)
                for k, v in src.attrs.items():
                    g.attrs[k] = v
                for name in src:
                    src.copy(name, g)
                n = src["trajectories"].shape[0] if "trajectories" in src else 0
                g.attrs["n_trajectories"] = n
                g.attrs["source_file"] = str(Path(p).resolve())
                ds = json.loads(src.attrs["datasets"]) if "datasets" in src.attrs else []
                datasets = datasets or ds
                streams.append(stream)
                print(f"{stream:44} {n:5d} trajectories  datasets={ds}")
        out.create_dataset("streams", data=[s.encode() for s in streams])
        out.attrs["datasets"] = json.dumps(datasets or [])
        out.attrs["merged_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        out.attrs["sources"] = json.dumps([str(Path(p).resolve()) for p in args.inputs])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
