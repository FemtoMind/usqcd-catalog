"""
Shared helpers for the usqcd-catalog scripts.

Nothing here talks to HPSS. The only network dependency is MetaCat, and it is
imported lazily so inventory/build steps work without the client installed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG_CONFIG = REPO_ROOT / "catalog.toml"


# ----------------------------------------------------------------- config I/O
def load_toml(path: str | os.PathLike) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_catalog_config(path: str | os.PathLike | None = None) -> dict:
    cfg = load_toml(path or DEFAULT_CATALOG_CONFIG)
    cfg.setdefault("defaults", {})
    cfg["defaults"].setdefault("work_dir", "work")
    return cfg


# File-classification rules used when an ensemble TOML has no [[files]] table.
# They describe the NPLQCD/Chroma layout: <dir>/cfgs/<prefix>_cfg_N.lime etc.
DEFAULT_FILE_RULES = [
    {"pattern": r"^cfgs/.+_cfg_(?P<trajectory>\d+)\.lime$", "kind": "gauge_config",
     "format": "application/x-lime",
     "description": "Gauge configuration in SciDAC/ILDG LIME format."},
    {"pattern": r"^cfgs/.+_restart_(?P<trajectory>\d+)\.xml$", "kind": "hmc_input",
     "format": "application/xml",
     "description": "HMC restart input XML for this configuration."},
    {"pattern": r"^logs?\.(?P<trajectory>\d+)\.tar$", "kind": "hmc_log_archive",
     "format": "application/x-tar",
     "description": "Tarball of HMC logs and inline measurements up to this trajectory."},
]


def load_ensemble(path: str | os.PathLike) -> dict:
    """Load an ensemble TOML and fill in defaults.

    The only required value is [ensemble].name. Everything else is optional:
      [lqcd]     physics/algorithm fields, any subset; flattened to lqcd.* keys
      [streams]  defaults to a single stream "main" living in <root>/<name>
      [[files]]  defaults to DEFAULT_FILE_RULES (NPLQCD/Chroma layout)
    """
    ens = load_toml(path)
    if "ensemble" not in ens or "name" not in ens["ensemble"]:
        sys.exit(f"{path}: [ensemble] with a name is required")
    name = ens["ensemble"]["name"]
    ens.setdefault("lqcd", {})
    if not ens.get("streams"):
        ens["streams"] = {"main": {"dir": name}}
        sys.stderr.write(f"[info] no [streams] table; assuming one stream 'main' in directory {name}\n")
    for label, st in ens["streams"].items():
        st.setdefault("dir", name if label == "main" else f"{name}-{label}")
    if not ens.get("files"):
        ens["files"] = [dict(r) for r in DEFAULT_FILE_RULES]
        sys.stderr.write("[info] no [[files]] rules; using the default NPLQCD/Chroma layout rules\n")
    return ens


# -------------------------------------------------- mass <-> kappa (Wilson-type)
# Isotropic Wilson/clover: kappa = 1 / (2 (m0 + 4)), m0 the bare mass in lattice
# units as Chroma's <Mass>. Not valid for anisotropic actions (xi, nu enter) or
# for staggered/DWF, so it is only applied when the record says the fermion
# action is wilson/clover and not anisotropic.
WILSON_LIKE = {"wilson", "clover", "isoclover", "clover_wilson", "wilson_clover", "twisted_mass"}
FLAVORS = ("light", "strange", "charm", "up", "down", "bottom")


def kappa_from_mass(m0: float) -> float:
    return 1.0 / (2.0 * (m0 + 4.0))


def mass_from_kappa(kappa: float) -> float:
    return 1.0 / (2.0 * kappa) - 4.0


def wilson_like(md: dict) -> bool:
    act = str(md.get("lqcd.action.fermion", "")).lower()
    aniso = md.get("lqcd.action.anisotropic", False)
    return act in WILSON_LIKE and not aniso


def complete_mass_kappa(md: dict) -> dict:
    """Given flattened lqcd.* metadata with lqcd.mass.<flavor> and/or
    lqcd.kappa.<flavor>, fill in the missing partner for Wilson-like actions and
    record which one was the input in lqcd.mass.input_convention.
    Returns the same dict (mutated)."""
    if not wilson_like(md):
        return md
    conv = None
    for fl in FLAVORS:
        mk, kk = f"lqcd.mass.{fl}", f"lqcd.kappa.{fl}"
        if mk in md and kk not in md:
            md[kk] = round(kappa_from_mass(float(md[mk])), 12)
            conv = conv or "mass"
        elif kk in md and mk not in md:
            md[mk] = round(mass_from_kappa(float(md[kk])), 12)
            conv = conv or "kappa"
        elif mk in md and kk in md:
            conv = conv or "both"
    if conv:
        md.setdefault("lqcd.mass.input_convention", conv)
    return md


def work_dir(cfg: dict, ensemble_name: str) -> Path:
    d = Path(cfg["defaults"]["work_dir"])
    if not d.is_absolute():
        d = REPO_ROOT / d
    d = d / ensemble_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def storage_for(cfg: dict, ens: dict) -> dict:
    key = ens["ensemble"].get("storage")
    try:
        return dict(cfg["storage"][key], key=key)
    except KeyError:
        sys.exit(f"ensemble.storage = {key!r} is not defined in catalog.toml [storage.*]")


# ------------------------------------------------------------ metadata shape
def flatten(d: dict, prefix: str) -> dict:
    """{'a': {'b': 1}, 'c': 2} with prefix 'lqcd' -> {'lqcd.a.b': 1, 'lqcd.c': 2}.

    Lists are kept as values (MetaCat stores JSON arrays); nested dicts become
    dotted keys so every value is queryable in MQL.
    """
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


def dataset_did(cfg: dict, ensemble_name: str) -> str:
    return f'{cfg["metacat"]["namespace"]}:{ensemble_name}'


def classify(rel_path: str, rules: list[dict]) -> tuple[dict | None, dict]:
    """Return (rule, named-groups) for the first [[files]] rule matching rel_path."""
    for rule in rules:
        m = re.match(rule["pattern"], rel_path)
        if m:
            return rule, {k: v for k, v in m.groupdict().items() if v is not None}
    return None, {}


# ---------------------------------------------------------------- MetaCat
def metacat_client(cfg: dict, need_auth: bool):
    """Return a MetaCatClient. With need_auth, exit with a clear message if the
    stored token is missing/expired (reads do not need one)."""
    url = cfg["metacat"]["server_url"]
    os.environ.setdefault("METACAT_SERVER_URL", url)
    try:
        from metacat.webapi import MetaCatClient
    except ImportError:
        sys.exit(
            "metacat-client is not importable. Run this script with the interpreter "
            f'named in catalog.toml [venv].python ({cfg.get("venv", {}).get("python", "?")}).'
        )
    client = MetaCatClient(url)
    if need_auth:
        try:
            user, expiry = client.auth_info()
        except Exception as e:  # AuthenticationError, missing token file, ...
            helper = cfg["metacat"].get("auth_helper", "metacat auth login")
            sys.exit(
                f"MetaCat token missing or expired ({e}).\n"
                f"Get one by running (interactive):  ! {helper}\n"
                "then re-run this command."
            )
        try:
            expiry = datetime.fromtimestamp(float(expiry)).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass
        sys.stderr.write(f"[auth] MetaCat user {user}, token valid until {expiry}\n")
    return client


# ---------------------------------------------------------------- misc
def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if x < 1024 or unit == "PB":
            return f"{x:.1f} {unit}" if unit != "B" else f"{int(x)} B"
        x /= 1024
    return f"{n} B"


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=False, default=str)
        f.write("\n")


def read_json(path: str | os.PathLike):
    with open(path) as f:
        return json.load(f)
