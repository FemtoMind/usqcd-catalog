"""
The catalog is the ensemble registry; ensembles/ and derived_datasets/ are
generated snapshots of it. This module holds what the registry scripts share.

Registry fields on a dataset record (schema_version 2):

  lqcd.catalog.schema_version   2
  lqcd.catalog.revision         "<UTC time>/<user>", new on every registry write;
                                a local snapshot records the revision it was
                                synced at, and writers refuse a stale snapshot
  lqcd.catalog.source           the description as JSON, without [streams]
                                and [catalog] (physics, observables, file rules,
                                provenance)
  lqcd.catalog.inherits_streams true for a derived dataset whose description
                                takes its streams from the parent
  lqcd.streams.<label>.<field>  one key per stream field (dir, parent_stream,
                                parent_trajectory, exclude, registered_by,
                                registered_at), so concurrent stream additions
                                touch disjoint keys
  lqcd.stream_labels            the stream labels on the file records, for
                                one-line queries; always merged, never shrunk

Stream labels and ensemble names are immutable once registered.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from catalog_common import REPO_ROOT, utcnow

SCHEMA_VERSION = 2
LABEL_OK = re.compile(r"^[A-Za-z0-9_-]+$")          # stream labels: TOML bare keys and MetaCat key segments
STREAM_FIELD_ORDER = ("dir", "parent_stream", "parent_trajectory", "exclude", "registered_by", "registered_at")
REGISTRY_TABLE = "catalog"                           # [catalog] in a snapshot: bookkeeping, not description


# ------------------------------------------------------------ record <-> description
def new_revision(user: str) -> str:
    return f"{utcnow()}/{user}"


def stream_sort_key(item):
    label, st = item
    nat = [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", label)]
    return (st.get("registered_at", ""), label != "main", nat)


TOP_ORDER = ["ensemble", "lqcd", "derived", "observables", "streams", "files", REGISTRY_TABLE]


def canonical(raw: dict) -> dict:
    """Top-level tables in a fixed order, so the stored source and every
    snapshot are the same however the hand-written file was laid out."""
    return {k: raw[k] for k in TOP_ORDER if k in raw} | {k: v for k, v in raw.items() if k not in TOP_ORDER}


def registry_fields(raw: dict) -> dict:
    """Dataset metadata carrying the description `raw` (a TOML dict as written,
    before defaults are filled in). Stream labels are added by the caller."""
    source = {k: v for k, v in canonical(raw).items() if k not in ("streams", REGISTRY_TABLE)}
    md = {
        "lqcd.catalog.schema_version": SCHEMA_VERSION,
        "lqcd.catalog.source": json.dumps(source, separators=(",", ":")),
    }
    streams = raw.get("streams") or {}
    if raw.get("ensemble", {}).get("parent") and not streams:
        md["lqcd.catalog.inherits_streams"] = True
    for label, st in streams.items():
        for k, v in st.items():
            md[f"lqcd.streams.{label}.{k}"] = v
    return md


def streams_from_metadata(md: dict) -> dict:
    """{label: {field: value}} from the lqcd.streams.<label>.<field> keys."""
    out: dict[str, dict] = {}
    for k, v in md.items():
        if k.startswith("lqcd.streams.") and k.count(".") >= 3:
            _, _, label, field = k.split(".", 3)
            out.setdefault(label, {})[field] = v
    ordered = {}
    for label, st in sorted(out.items(), key=stream_sort_key):
        ordered[label] = {f: st[f] for f in STREAM_FIELD_ORDER if f in st}
        ordered[label].update({f: st[f] for f in sorted(st) if f not in STREAM_FIELD_ORDER})
    return ordered


def description_from_metadata(md: dict) -> dict:
    """The description (TOML dict) a dataset record carries, plus [catalog]."""
    if "lqcd.catalog.source" not in md:
        raise ValueError("record has no lqcd.catalog.source (written before registry schema 2)")
    raw = json.loads(md["lqcd.catalog.source"])
    if not md.get("lqcd.catalog.inherits_streams"):
        streams = streams_from_metadata(md)
        if streams:
            raw["streams"] = streams
    raw[REGISTRY_TABLE] = {"revision": md.get("lqcd.catalog.revision", ""),
                           "schema_version": md.get("lqcd.catalog.schema_version", SCHEMA_VERSION)}
    return canonical(raw)


def snapshot_path(name: str, raw: dict) -> Path:
    sub = "derived_datasets" if raw.get("ensemble", {}).get("parent") else "ensembles"
    return REPO_ROOT / sub / f"{name}.toml"


# ------------------------------------------------------------ TOML writer
_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE.match(k) else json.dumps(k)


def _str(s: str, multiline_ok: bool = False) -> str:
    if "\n" in s and multiline_ok and "'''" not in s:
        return "'''\n" + s + "'''"
    if "\\" in s and "'" not in s and "\n" not in s:
        return "'" + s + "'"                        # literal string: regexes stay readable
    if multiline_ok and len(s) > 88 and "\n" not in s:
        return _wrapped(s)
    return json.dumps(s, ensure_ascii=False)


def _wrapped(s: str, width: int = 76) -> str:
    """Multi-line basic string with line-ending backslashes: the value has no
    newlines, the file stays readable."""
    esc = json.dumps(s, ensure_ascii=False)[1:-1]
    words, lines, cur = esc.split(" "), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur + " \\")
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    lines.append(cur)
    return '"""\\\n' + "\n".join(lines) + '"""'


def _val(v, multiline_ok: bool = False) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _str(v, multiline_ok)
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_val(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def _table(path: list[str], d: dict, out: list[str]) -> None:
    scalars = {k: v for k, v in d.items() if not isinstance(v, dict) and not _is_aot(v)}
    subs = {k: v for k, v in d.items() if isinstance(v, dict)}
    aots = {k: v for k, v in d.items() if _is_aot(v)}
    if scalars or not (subs or aots):
        out.append("[" + ".".join(_key(p) for p in path) + "]")
        for k, v in scalars.items():
            out.append(f"{_key(k)} = {_val(v, multiline_ok=True)}")
        out.append("")
    for k, v in subs.items():
        _table(path + [k], v, out)
    for k, v in aots.items():
        for item in v:
            out.append("[[" + ".".join(_key(p) for p in path + [k]) + "]]")
            for ik, iv in item.items():
                out.append(f"{_key(ik)} = {_val(iv, multiline_ok=True)}")
            out.append("")


def _is_aot(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def to_toml(raw: dict, header: list[str] | None = None) -> str:
    out = [f"# {h}" if h else "#" for h in (header or [])]
    if out:
        out.append("")
    for k, v in raw.items():
        if isinstance(v, dict):
            _table([k], v, out)
        elif _is_aot(v):
            for item in v:
                out.append(f"[[{_key(k)}]]")
                for ik, iv in item.items():
                    out.append(f"{_key(ik)} = {_val(iv, multiline_ok=True)}")
                out.append("")
        else:
            raise ValueError(f"top-level key {k!r} is not a table")
    return "\n".join(out).rstrip("\n") + "\n"


def snapshot_text(name: str, ns: str, raw: dict) -> str:
    header = [
        f"Registry snapshot of {ns}:{name}, generated by scripts/sync_registry.py.",
        "The catalog is the source of truth: change it with the registry scripts",
        "(add_stream.py, register_ensemble.py, update_ensemble.py), not by editing",
        "this file; the next sync overwrites hand edits. Provenance notes live in",
        "[ensemble] provenance.",
    ]
    return to_toml(raw, header)


# ------------------------------------------------------------ comments -> provenance
def comments_to_provenance(text: str) -> str:
    """All '#' comments of a hand-written TOML, in order, as one text block
    (the leading header block first). Comment markers inside strings are
    ignored by only looking at lines that start with '#' or at a ' # ' after
    a closing quote/bracket/number."""
    lines = []
    in_multiline = False
    for line in text.splitlines():
        s = line.strip()
        if s.count('"""') % 2 == 1 or s.count("'''") % 2 == 1:
            in_multiline = not in_multiline
            continue
        if in_multiline:
            continue
        if s.startswith("#"):
            lines.append(s[1:][1:] if s.startswith("# ") else s[1:])
        else:
            m = re.search(r"""(?<=[\]"'\w})])\s+#\s?(.*)$""", line)
            if m and not s.startswith("pattern"):
                lines.append(m.group(1))
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip("\n") + ("\n" if lines else "")


# ------------------------------------------------------------ guards
def check_schema(md: dict, what: str) -> None:
    v = md.get("lqcd.catalog.schema_version", 1)
    if isinstance(v, int) and v > SCHEMA_VERSION:
        sys.exit(f"{what} uses registry schema version {v}; these scripts know version {SCHEMA_VERSION}. "
                 "Update your checkout (git pull) before writing to it.")


def check_owner(ds: dict, user: str, force: bool, what: str) -> None:
    owner = ds.get("creator")
    if owner and owner != user and not force:
        when = time.strftime("%Y-%m-%d", time.gmtime(ds.get("created_timestamp") or 0))
        sys.exit(f"{what} was registered by {owner!r} on {when}, not by you ({user!r}).\n"
                 f"Changing another group's ensemble needs their agreement; if you have it, re-run with --force.\n"
                 f"Contact: the {owner!r} account in the FNAL Services directory, or the lqcd namespace owners.")
    if owner and owner != user:
        sys.stderr.write(f"[warn] --force: modifying {what}, registered by {owner!r}\n")


def check_stream_owner(md: dict, labels: list[str], user: str, force: bool, what: str) -> None:
    others = {l: md.get(f"lqcd.streams.{l}.registered_by") for l in labels}
    others = {l: o for l, o in others.items() if o and o != user}
    if others and not force:
        lst = ", ".join(f"{l} ({o})" for l, o in others.items())
        sys.exit(f"{what}: streams registered by other users would change: {lst}.\n"
                 "Re-run with --force if they agree.")
    for l, o in others.items():
        sys.stderr.write(f"[warn] --force: changing stream {l!r} of {what}, registered by {o!r}\n")


def check_revision(md: dict, local_revision: str | None, force: bool, what: str, name: str) -> None:
    remote = md.get("lqcd.catalog.revision")
    if remote and local_revision != remote and not force:
        sys.exit(f"{what} changed in the catalog after your records were built (someone else wrote to it)\n"
                 f"  catalog revision: {remote}\n  your revision:    {local_revision or '(none)'}\n"
                 f"Re-run build_records.py (it picks up the current registry entry) and declare again "
                 "(or --force to overwrite).")


def merged_labels(remote: list | None, local: list) -> list:
    out = list(remote or [])
    out += [l for l in local if l not in out]
    return out
