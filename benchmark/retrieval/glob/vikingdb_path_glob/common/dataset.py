# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deterministic synthetic dataset for the VikingDB path_glob benchmark.

The dataset is a single tree of 1,000,000 one-byte files laid out to look like
a realistic multi-service monorepo. It is split into disjoint top-level "scale
buckets" so that globbing a given bucket path yields an exact data volume. This
lets a single dataset serve every scale (500 .. 1,000,000) and lets ``auto``
(remote VikingDB) and ``fs`` (local filesystem) glob be compared on the
identical URI set.

Layout goals (to stay close to real business usage):

* Variable directory depth. Files land in shallow spots (``tools/x.sh``, one
  level) up to deep language-partitioned trees
  (``services/auth/src/main/python/acme/platform/mod_03/x.py``, eight levels),
  drawn from weighted path templates so depth has real variance rather than a
  single fixed shape.
* A realistic vocabulary of areas (``services``/``libs``/``apps``/``docs``/
  ``tools``), project names, layers, modules and language partitions.
* An extension mix that supports both bounded default patterns and opt-in
  broad-result stress patterns. See ``common/patterns.py``.
* Two fixed "landmark" subtrees carrying exact small counts (10 ``migrations``
  ``.sql`` files, 100 ``legacy-gateway`` ``.go`` files) so absolute-count
  patterns (~10, ~100) have precise ground truth. They live in the padding
  bucket, which only appears under the whole-tree (100w) glob root.

Why one byte per file: glob only matches on URI / path, never on file content or
vectors. Empty (0-byte) files are skipped by the importer
(``openviking/parse/parsers/upload_utils.py`` treats ``size == 0`` as an empty
file), so each file carries exactly one filler byte to stay importable while
keeping generation fast and disk usage tiny (~1 MB of content for 1M files).

Ground truth for effectiveness is derived purely from the manifest written here,
not from either engine, so both engines can be scored against the same truth.

IMPORTANT — ground truth is the *post-import* layout, not the on-disk layout.
The importer routes each file to a parser by extension (see
``openviking/parse/registry.py``). Code/config/data files (``.py``/``.ts``/
``.go``/``.js``/``.json``/``.yaml``/``.toml``/``.proto``/``.sql``/``.sh``)
have no dedicated markdown/text parser rewrite in this benchmark and are written
verbatim, so their URI equals their disk path. Markdown/text suffixes are
intentionally excluded so 1,000,000 source files become exactly 1,000,000 final
Workspace files and 1,000,000 VikingDB L2 records.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import dataclass
from typing import Callable, Iterator, List, Tuple

# One filler byte per file. Anything non-empty works; the byte is never matched.
FILE_CONTENT = "x"


@dataclass(frozen=True)
class ScaleBucket:
    """A disjoint top-level directory holding a fixed number of files."""

    name: str
    count: int


# Disjoint buckets. Their sizes are chosen so that individual buckets cover the
# small/medium scales exactly, and the whole tree (root) covers 1,000,000.
#   500 + 1,000 + 2,000 + 5,000 + 100,000 + 200,000 + 500,000 = 808,500
#   scale_fill pads the remainder: 1,000,000 - 808,500 = 191,500
SCALE_BUCKETS: List[ScaleBucket] = [
    ScaleBucket("scale_500", 500),
    ScaleBucket("scale_1k", 1_000),
    ScaleBucket("scale_2k", 2_000),
    ScaleBucket("scale_5k", 5_000),
    ScaleBucket("scale_10w", 100_000),
    ScaleBucket("scale_20w", 200_000),
    ScaleBucket("scale_50w", 500_000),
    ScaleBucket("scale_fill", 191_500),
]

TOTAL_FILES = sum(bucket.count for bucket in SCALE_BUCKETS)  # 1,000,000

# Exact nested query roots inside scale_500. The values are disjoint slices of
# the deterministic pre-layout manifest; the remaining 140 paths stay where
# they were. This keeps scale_500 at 500 files while adding exact small scales.
SCALE_500_SUBBUCKETS: List[ScaleBucket] = [
    ScaleBucket("scale_10", 10),
    ScaleBucket("scale_50", 50),
    ScaleBucket("scale_100", 100),
    ScaleBucket("scale_200", 200),
]
SCALE_500_SUBBUCKET_TOTAL = sum(bucket.count for bucket in SCALE_500_SUBBUCKETS)

# Named scales -> (relative bucket path used as glob uri suffix, file count).
# The special scale "100w" globs the whole tree (root), which spans all buckets.
SCALES: List[Tuple[str, str, int]] = [
    ("10", "scale_500/scale_10", 10),
    ("50", "scale_500/scale_50", 50),
    ("100", "scale_500/scale_100", 100),
    ("200", "scale_500/scale_200", 200),
    ("500", "scale_500", 500),
    ("1k", "scale_1k", 1_000),
    ("2k", "scale_2k", 2_000),
    ("5k", "scale_5k", 5_000),
    ("10w", "scale_10w", 100_000),
    ("20w", "scale_20w", 200_000),
    ("50w", "scale_50w", 500_000),
    ("100w", "", TOTAL_FILES),  # empty suffix -> the dataset root
]

# --------------------------------------------------------------------------- #
# Extension mix
# --------------------------------------------------------------------------- #
# Weights are per-1000 so alternation patterns land on clean recall ratios:
#   py+ts+go                    = 500  -> ~50% (**/*.{py,ts,go})
#   + js + config/script group  = 900  -> ~90% (**/*.{py,ts,go,js,json,yaml,toml,proto,sh})
#   json+yaml+toml+proto+sh     = 100  -> ~10% (**/*.{json,yaml,toml,proto,sh})
#   proto                       =  10  -> ~1%  (**/*.proto)
_EXTENSION_WEIGHTS: List[Tuple[int, str]] = [
    (300, "py"),
    (120, "ts"),
    (80, "go"),
    (300, "js"),
    (50, "json"),
    (15, "yaml"),
    (10, "toml"),
    (10, "proto"),
    (15, "sh"),
    (100, "sql"),
]
_EXT_CHOICES = [ext for _weight, ext in _EXTENSION_WEIGHTS]
_EXT_WEIGHTS = [weight for weight, _ext in _EXTENSION_WEIGHTS]
GENERATED_EXTENSIONS = frozenset(_EXT_CHOICES)
SPECIAL_PARSER_EXTENSIONS = frozenset({"md", "markdown", "mdown", "mkd", "txt", "text"})

# Extensions treated as source code (eligible for a ``tests/`` placement).
_CODE_EXTS = frozenset({"py", "ts", "go", "js"})

# --------------------------------------------------------------------------- #
# Path vocabulary (monorepo-like)
# --------------------------------------------------------------------------- #
_SERVICES = [
    "auth",
    "payment",
    "billing",
    "user",
    "order",
    "catalog",
    "search",
    "notification",
    "analytics",
    "reporting",
    "inventory",
    "shipping",
    "pricing",
    "recommendation",
    "identity",
    "checkout",
]
_LIBS = ["common", "core", "utils", "models", "config", "logging", "metrics", "testing"]
_APPS = ["web", "admin", "mobile-api", "dashboard"]
_LAYERS = ["api", "service", "repository", "handler", "controller", "worker", "client", "internal"]
_SUBS = ["handlers", "models", "types", "helpers", "internal", "adapters"]
_MODULES = [f"mod_{i:02d}" for i in range(20)]  # mod_00 .. mod_19
_LANGDIRS = ["go", "python", "typescript", "java"]
_GROUPS = ["platform", "domain", "shared", "core"]
_ORG = "acme"
_DOC_TOPICS = ["guides", "reference", "tutorials", "architecture", "api"]

# Probability a config file lives under a ``docs/`` topic dir (else general).
_DOCS_PROB = 0.55
# Probability a code file lives under a ``tests/`` dir as ``test_*``.
_TEST_PROB = 0.28


def _tmpl_tools(rng: random.Random, leaf: str) -> str:
    return f"tools/{leaf}"


def _tmpl_apps(rng: random.Random, leaf: str) -> str:
    return f"apps/{rng.choice(_APPS)}/{leaf}"


def _tmpl_libs(rng: random.Random, leaf: str) -> str:
    return f"libs/{rng.choice(_LIBS)}/src/{leaf}"


def _tmpl_svc4(rng: random.Random, leaf: str) -> str:
    return f"services/{rng.choice(_SERVICES)}/src/{rng.choice(_MODULES)}/{leaf}"


def _tmpl_svc5(rng: random.Random, leaf: str) -> str:
    return (
        f"services/{rng.choice(_SERVICES)}/src/{rng.choice(_LAYERS)}/{rng.choice(_MODULES)}/{leaf}"
    )


def _tmpl_svc6(rng: random.Random, leaf: str) -> str:
    return (
        f"services/{rng.choice(_SERVICES)}/src/{rng.choice(_LAYERS)}/"
        f"{rng.choice(_MODULES)}/{rng.choice(_SUBS)}/{leaf}"
    )


def _tmpl_svc8(rng: random.Random, leaf: str) -> str:
    return (
        f"services/{rng.choice(_SERVICES)}/src/main/{rng.choice(_LANGDIRS)}/"
        f"{_ORG}/{rng.choice(_GROUPS)}/{rng.choice(_MODULES)}/{leaf}"
    )


# General source-file templates (name, builder, directory-level count) plus a
# relative weight. Directory levels are for documentation only; the weights
# create a depth distribution spanning 1..8 levels centred around 4-5.
_GENERAL_TEMPLATES: List[Tuple[Callable[[random.Random, str], str], int, int]] = [
    (_tmpl_tools, 1, 5),
    (_tmpl_apps, 2, 9),
    (_tmpl_libs, 3, 16),
    (_tmpl_svc4, 4, 24),
    (_tmpl_svc5, 5, 20),
    (_tmpl_svc6, 6, 14),
    (_tmpl_svc8, 8, 12),
]
_GEN_BUILDERS = [builder for builder, _levels, _weight in _GENERAL_TEMPLATES]
_GEN_WEIGHTS = [weight for _builder, _levels, weight in _GENERAL_TEMPLATES]


def _stable_seed(seed: int, bucket_name: str) -> int:
    """Deterministic per-bucket integer seed independent of PYTHONHASHSEED."""
    digest = hashlib.md5(f"{seed}:{bucket_name}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _leaf_name(stem: str, ext: str, prefix: str = "") -> str:
    return f"{prefix}{stem}.{ext}"


def _random_relpath(index: int, rng: random.Random) -> str:
    """Build one repo-like relative path for a file at ``index`` in a bucket.

    Path shape (and therefore depth) is drawn from weighted templates so the
    tree has realistic depth variance. ``stem`` embeds the index, keeping every
    path unique within a bucket regardless of which template is chosen.
    """
    ext = rng.choices(_EXT_CHOICES, weights=_EXT_WEIGHTS, k=1)[0]
    stem = f"file_{index:07d}"

    # Config files often live under docs/<topic>/ (two levels).
    if ext == "json" and rng.random() < _DOCS_PROB:
        leaf = _leaf_name(stem, "json")
        return f"docs/{rng.choice(_DOC_TOPICS)}/{leaf}"

    # Code files sometimes live under <service>/tests/ as test_<name> (three
    # levels). This backs the ``**/tests/test_*.py`` structural pattern.
    if ext in _CODE_EXTS and rng.random() < _TEST_PROB:
        leaf = _leaf_name(stem, ext, prefix="test_")
        return f"services/{rng.choice(_SERVICES)}/tests/{leaf}"

    leaf = _leaf_name(stem, ext)
    builder = rng.choices(_GEN_BUILDERS, weights=_GEN_WEIGHTS, k=1)[0]
    return builder(rng, leaf)


# --------------------------------------------------------------------------- #
# Landmark subtrees (exact small counts for absolute-count patterns)
# --------------------------------------------------------------------------- #
# These are placed in the padding bucket (``scale_fill``), which is only visible
# under the whole-tree 100w glob root, so absolute-count patterns are scored
# against the full 1M dataset without polluting the single-bucket scales.
LANDMARK_HOST_BUCKET = "scale_fill"
LANDMARK_MIGRATIONS = 10  # matched by **/migrations/*.sql
LANDMARK_GATEWAY = 100  # matched by **/legacy-gateway/**/*.go
LANDMARK_TOTAL = LANDMARK_MIGRATIONS + LANDMARK_GATEWAY

_MIGRATION_NAMES = [
    "init_schema",
    "add_users",
    "add_orders",
    "index_orders",
    "add_payments",
    "backfill_totals",
    "add_audit_log",
    "drop_legacy_cols",
    "add_shipping",
    "finalize",
]


def iter_landmark_relpaths() -> Iterator[str]:
    """Yield the fixed landmark relative paths (bucket-relative)."""
    for i in range(1, LANDMARK_MIGRATIONS + 1):
        name = _MIGRATION_NAMES[(i - 1) % len(_MIGRATION_NAMES)]
        yield f"services/platform-core/db/migrations/{i:04d}_{name}.sql"
    for i in range(LANDMARK_GATEWAY):
        layer = _LAYERS[i % len(_LAYERS)]
        yield f"services/legacy-gateway/src/{layer}/gw_{i:03d}.go"


def iter_original_bucket_relpaths(bucket_name: str, count: int, seed: int) -> Iterator[str]:
    """Yield the deterministic bucket paths before nested-scale layout.

    Paths are relative to the bucket directory. The full relative path from the
    dataset root is ``f"{bucket_name}/{relpath}"``. The landmark host bucket
    reserves ``LANDMARK_TOTAL`` of its ``count`` for the fixed landmark subtrees.
    """
    rng = random.Random(_stable_seed(seed, bucket_name))
    if bucket_name == LANDMARK_HOST_BUCKET and count >= LANDMARK_TOTAL:
        random_count = count - LANDMARK_TOTAL
        for index in range(random_count):
            yield _random_relpath(index, rng)
        yield from iter_landmark_relpaths()
    else:
        for index in range(count):
            yield _random_relpath(index, rng)


def iter_scale_500_moves(count: int, seed: int) -> Iterator[Tuple[str, str]]:
    """Yield source/target paths needed to upgrade an existing scale_500."""
    if count < SCALE_500_SUBBUCKET_TOTAL:
        raise ValueError(f"scale_500 needs at least {SCALE_500_SUBBUCKET_TOTAL} files, got {count}")
    original = iter_original_bucket_relpaths("scale_500", count, seed)
    remaining = SCALE_500_SUBBUCKET_TOTAL
    for subbucket in SCALE_500_SUBBUCKETS:
        for _ in range(subbucket.count):
            source = next(original)
            yield source, f"{subbucket.name}/{source}"
            remaining -= 1
    assert remaining == 0


def iter_bucket_relpaths(bucket_name: str, count: int, seed: int) -> Iterator[str]:
    """Yield final-layout relative paths for one bucket.

    ``scale_500`` starts with four exact nested scales; its final 140 paths keep
    their original locations. Other buckets are unchanged.
    """
    original = iter_original_bucket_relpaths(bucket_name, count, seed)
    if bucket_name != "scale_500" or count < SCALE_500_SUBBUCKET_TOTAL:
        yield from original
        return

    for subbucket in SCALE_500_SUBBUCKETS:
        for _ in range(subbucket.count):
            yield f"{subbucket.name}/{next(original)}"
    yield from original


def iter_scale_relpaths(scale: str, seed: int) -> Iterator[str]:
    """Yield final paths relative to the query root for a named scale."""
    try:
        bucket, count = next((bucket, count) for name, bucket, count in SCALES if name == scale)
    except StopIteration as exc:
        raise ValueError(f"unknown scale: {scale}") from exc

    if not bucket:
        for spec in SCALE_BUCKETS:
            for relpath in iter_bucket_relpaths(spec.name, spec.count, seed):
                yield f"{spec.name}/{relpath}"
        return

    root_bucket, separator, nested_root = bucket.partition("/")
    root_count = next(spec.count for spec in SCALE_BUCKETS if spec.name == root_bucket)
    paths = iter_bucket_relpaths(root_bucket, root_count, seed)
    if not separator:
        yield from paths
        return

    prefix = nested_root.rstrip("/") + "/"
    for path in paths:
        if path.startswith(prefix):
            yield path[len(prefix) :]


# --------------------------------------------------------------------------- #
# Manifest I/O
# --------------------------------------------------------------------------- #
# The manifest is stored as one newline-delimited file per bucket plus an
# index.json header. This avoids holding 1M paths in one JSON blob and lets
# callers stream a single bucket's paths on demand.


def manifest_dir(output_dir: str) -> str:
    return os.path.join(output_dir, "manifest")


def manifest_index_path(output_dir: str) -> str:
    return os.path.join(manifest_dir(output_dir), "index.json")


def bucket_manifest_path(output_dir: str, bucket_name: str) -> str:
    return os.path.join(manifest_dir(output_dir), f"{bucket_name}.txt")


def write_manifest_index(output_dir: str, seed: int) -> None:
    os.makedirs(manifest_dir(output_dir), exist_ok=True)
    payload = {
        "seed": seed,
        "total_files": TOTAL_FILES,
        "buckets": [{"name": b.name, "count": b.count} for b in SCALE_BUCKETS],
        "scales": [
            {"scale": scale, "bucket": bucket, "count": count} for scale, bucket, count in SCALES
        ],
    }
    with open(manifest_index_path(output_dir), "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)


def load_manifest_index(output_dir: str) -> dict:
    with open(manifest_index_path(output_dir), encoding="utf-8") as file:
        return json.load(file)


def load_bucket_relpaths(output_dir: str, bucket_name: str) -> List[str]:
    """Load the relative paths of a single bucket from its manifest file."""
    path = bucket_manifest_path(output_dir, bucket_name)
    with open(path, encoding="utf-8") as file:
        return [line.rstrip("\n") for line in file if line.rstrip("\n")]


# --------------------------------------------------------------------------- #
# Post-import path transform (ground truth alignment)
# --------------------------------------------------------------------------- #
# This v2 dataset intentionally avoids markdown/text parser suffixes, so every
# generated source path is also the post-import Workspace/VikingDB URI path.


def imported_relpath(rel_path: str) -> str:
    """Map an on-disk relative path to the URI the server stores after import.

    The v2 generator only emits suffixes whose import path is identity.
    """
    return rel_path


def load_scale_relpaths(output_dir: str, scale: str) -> List[str]:
    """Load post-import relative paths *relative to the scale's glob root*.

    A single-bucket scale globs the bucket directory itself, so its paths are
    bucket-relative (``services/auth/src/...``). The ``100w`` scale globs the
    dataset root, so its paths are prefixed with the bucket name
    (``scale_500/services/auth/src/...``). Callers build full URIs by joining
    the scale's base URI with these.

    Paths are passed through :func:`imported_relpath` so the returned set matches
    what the server actually stores (markdown/text files are wrapped/renamed),
    keeping ground truth aligned with both glob faces.
    """
    index = load_manifest_index(output_dir)
    scale_to_bucket = {entry["scale"]: entry["bucket"] for entry in index["scales"]}
    if scale not in scale_to_bucket:
        raise ValueError(f"unknown scale: {scale}")

    bucket = scale_to_bucket[scale]
    if bucket:  # single-bucket scale: root is the bucket dir
        root_bucket, separator, nested_root = bucket.partition("/")
        paths = load_bucket_relpaths(output_dir, root_bucket)
        if separator:
            prefix = nested_root.rstrip("/") + "/"
            paths = [path[len(prefix) :] for path in paths if path.startswith(prefix)]
        return [imported_relpath(rel) for rel in paths]

    # Root scale (100w): root is the dataset root, so prefix each bucket name.
    all_paths: List[str] = []
    for entry in index["buckets"]:
        name = entry["name"]
        all_paths.extend(
            f"{name}/{imported_relpath(rel)}" for rel in load_bucket_relpaths(output_dir, name)
        )
    return all_paths
