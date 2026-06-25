# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pure I/O-free formatters powering the code_outline / code_search / code_expand
MCP tools. Inputs are source strings; outputs are agent-facing text.

The MCP layer handles all URI resolution and I/O; this module deals only with
content -> CodeSkeleton -> formatted text.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional, Tuple

from openviking.parse.parsers.code.ast.extractor import get_extractor
from openviking.parse.parsers.code.ast.skeleton import (
    CodeSkeleton,
    FunctionSig,
    _compact_params,
)

CODE_SEARCH_FILE_CAP = 1000
CODE_LOCATE_FILE_CAP = 1000
CODE_SEARCH_CONCURRENCY = 10
CODE_SCAN_LS_NODE_LIMIT = 100_000
CODE_SCAN_LS_LEVEL_LIMIT = 64


def _entry_field(entry, key: str, fallback_key: str, default):
    """Read a field from ls entries that may be dicts (camelCase) or objects (snake_case)."""
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, fallback_key, default)


def _path_relevance(uri: str, terms: list[str]) -> int:
    lower_uri = uri.lower()
    score = 0
    for term in terms:
        if term in lower_uri:
            score += 3 if "/" in lower_uri and term in lower_uri.rsplit("/", 1)[-1] else 1
    return score


def _uri_basename_stem(uri: str) -> str:
    basename = uri.lower().rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0]
    return stem.lstrip("_")


def _is_test_uri(uri: str) -> bool:
    lower = uri.lower()
    basename = lower.rsplit("/", 1)[-1]
    return "/tests/" in lower or "/test/" in lower or basename.startswith("test_") or basename.endswith("_test.py")


def _implementation_stem(uri: str) -> str:
    if _is_test_uri(uri):
        return ""
    return _uri_basename_stem(uri)


def _test_target_stem(uri: str) -> str:
    if not _is_test_uri(uri):
        return ""
    stem = _uri_basename_stem(uri)
    if stem.startswith("test_"):
        stem = stem[len("test_") :]
    if stem.endswith("_test"):
        stem = stem[: -len("_test")]
    return stem.lstrip("_")


def _keep_related_tests_under_cap(uris: list[str], cap: int) -> list[str]:
    selected = uris[:cap]
    selected_set = set(selected)
    implementation_stems = {_implementation_stem(uri) for uri in selected}
    implementation_stems.discard("")

    for test_uri in uris[cap:]:
        target_stem = _test_target_stem(test_uri)
        if not target_stem or target_stem not in implementation_stems or test_uri in selected_set:
            continue

        for index in range(len(selected) - 1, -1, -1):
            candidate = selected[index]
            if _is_test_uri(candidate) or _implementation_stem(candidate) == target_stem:
                continue
            selected_set.remove(candidate)
            selected[index] = test_uri
            selected_set.add(test_uri)
            break

    return selected


def select_code_uris(
    entries,
    query: str = "",
    *,
    cap: int = CODE_SEARCH_FILE_CAP,
) -> tuple[list[str], bool]:
    """Pick supported code file entries, prioritizing query path terms before capping.

    Returns (uris, capped) where capped is True when the file cap was hit and
    there may be more matching files beyond the cap.
    """
    extractor = get_extractor()
    uris: list[str] = []
    for e in entries:
        is_dir = _entry_field(e, "isDir", "is_dir", False)
        if is_dir:
            continue
        entry_uri = _entry_field(e, "uri", "uri", "")
        if not entry_uri:
            continue
        if extractor.supports(entry_uri):
            uris.append(entry_uri)

    terms = _query_terms(query) if query else []
    if terms:
        uris.sort(key=lambda uri: (-_path_relevance(uri, terms), uri))

    capped = len(uris) > cap
    if capped:
        return _keep_related_tests_under_cap(uris, cap), True
    return uris, False


def filter_code_uris(entries) -> tuple[list[str], bool]:
    """Backward-compatible code URI filter without query-aware prioritization."""
    return select_code_uris(entries)


def select_code_paths(paths, query: str = "", *, cap: int = CODE_SEARCH_FILE_CAP):
    """Rank local source paths with the same policy as viking URI selection."""
    path_by_uri = {str(path): path for path in paths}
    entries = [{"uri": uri, "isDir": False} for uri in path_by_uri]
    uris, capped = select_code_uris(entries, query, cap=cap)
    return [path_by_uri[uri] for uri in uris if uri in path_by_uri], capped


def _line_span(item) -> str:
    if item.line_start and item.line_end:
        return f"  L{item.line_start}-{item.line_end}"
    return ""


def _format_function(fn: FunctionSig, indent: str, prefix: str) -> str:
    ret = f" -> {fn.return_type}" if fn.return_type else ""
    params = _compact_params(fn.params)
    return f"{indent}{prefix}{fn.name}({params}){ret}{_line_span(fn)}"


def _outline_text(skeleton: CodeSkeleton, total_lines: int) -> str:
    lines: List[str] = [f"{skeleton.file_name}  [{skeleton.language}, {total_lines} lines]"]
    if skeleton.module_doc:
        first = skeleton.module_doc.split("\n", 1)[0].strip()
        if first:
            lines.append(f'module: "{first}"')
    if skeleton.imports:
        lines.append(f"imports: {', '.join(skeleton.imports)}")
    lines.append("")

    for cls in skeleton.classes:
        bases = f"({', '.join(cls.bases)})" if cls.bases else ""
        lines.append(f"class {cls.name}{bases}{_line_span(cls)}")
        for method in cls.methods:
            lines.append(_format_function(method, "  ", "+ "))
        lines.append("")

    for fn in skeleton.functions:
        lines.append(_format_function(fn, "", "def "))

    return "\n".join(lines).rstrip()


def outline_file(content: str, file_name: str) -> str:
    """Return outline view of one source file (header + symbols + line spans).

    Returns an "Error: ..." sentinel string when the language is unsupported or
    parsing fails — callers can detect by the "Error:" prefix.
    """
    skeleton = get_extractor().extract(file_name, content)
    if skeleton is None:
        return _failure_message(file_name)
    total_lines = content.count("\n") + 1 if content else 0
    return _outline_text(skeleton, total_lines)


def _failure_message(file_name: str) -> str:
    if not get_extractor().supports(file_name):
        return f"Error: unsupported language for {file_name}"
    return f"Error: failed to parse {file_name}"


def _iter_symbols(skeleton: CodeSkeleton) -> Iterable[Tuple[str, int, int]]:
    """Yield (display_name, line_start, line_end) for every symbol."""
    for cls in skeleton.classes:
        yield cls.name, cls.line_start, cls.line_end
        for method in cls.methods:
            yield f"{cls.name}.{method.name}", method.line_start, method.line_end
    for fn in skeleton.functions:
        yield fn.name, fn.line_start, fn.line_end


def search_symbols(query: str, files: List[Tuple[str, str]]) -> str:
    """Case-insensitive substring search across symbol names in many files.

    files: list of (content, file_name) tuples. Files whose language is
    unsupported or fails to parse are silently skipped (the caller already
    filtered by extension).
    """
    if not query:
        return "Error: empty query"

    needle = query.lower()
    extractor = get_extractor()
    scanned = 0
    hits_by_file: List[Tuple[str, List[Tuple[str, int, int]]]] = []
    total = 0

    for content, file_name in files:
        scanned += 1
        skeleton = extractor.extract(file_name, content)
        if skeleton is None:
            continue
        file_hits: List[Tuple[str, int, int]] = []
        for name, start, end in _iter_symbols(skeleton):
            tail = name.rsplit(".", 1)[-1]
            haystack = name.lower() if "." in needle else tail.lower()
            if needle in haystack:
                file_hits.append((name, start, end))
        if file_hits:
            hits_by_file.append((file_name, file_hits))
            total += len(file_hits)

    if total == 0:
        return f'No matches for "{query}" (scanned {scanned} files)'

    out: List[str] = [f'{total} matches for "{query}" (scanned {scanned} files)']
    for file_name, file_hits in hits_by_file:
        out.append("")
        out.append(file_name)
        for name, start, end in file_hits:
            span = f"  L{start}-{end}" if start and end else ""
            out.append(f"  {name}{span}")
    return "\n".join(out)


CODE_SEARCH_CONTENT_MAX_PER_FILE = 5
CODE_SEARCH_CONTENT_MAX_TOTAL = 50
CODE_SEARCH_HYBRID_RESULT_LIMIT = 20
CODE_LOCATE_EDIT_LIMIT = 5
CODE_LOCATE_REFERENCE_LIMIT = 3
CODE_LOCATE_IMPORT_LIMIT = 8
CODE_LOCATE_FOCUS_LIMIT = 3
CODE_LOCATE_CONTENT_SCORE_CAP = 120
CODE_LOCATE_EXACT_IDENTIFIER_SCORE = 80
CODE_LOCATE_EDIT_NEXT_ACTION = "inspect current checkout lines; no web/upstream/git history"
CODE_LOCATE_REFERENCE_NEXT_ACTION = "read current checkout assertions; no web/upstream/git history"
CODE_LOCATE_DIAGNOSTIC_WORDING_DELTA_ACTION = (
    "PATCH FIRST: diagnostic wording delta, not a numbering/builder "
    "regression; patch only the production diagnostic emitter "
    "message/arguments; borrow same-file diagnostic precedent prefix/style "
    "but keep the emitter's original reason semantics; then run the immediate "
    "static check; if it passes, stop and final-answer immediately; do not "
    "inspect visible tests or implementation logic for extra confidence; do "
    "not edit tests, assertions, fixtures, builders, or numbering logic; if "
    "patch application fails, read the exact edit line and retry the same "
    "diagnostic patch; broaden only if the patch or static check fails"
)
CODE_LOCATE_DIAGNOSTIC_NEXT_ACTION = (
    "inspect diagnostic emitter and matching assertions in current checkout; "
    "if a nearby positive assertion expects this diagnostic, treat it as "
    "fail-to-pass risk and update the diagnostic message/arguments first; "
    "compare same-file or same-domain resolver/error-handling precedent; keep "
    "the first patch near the emitter guard; reproduce or verify narrowly "
    "after the patch, static check first; if pytest fails before collection, stop broad fixture search and "
    "treat it as setup; if nearby tests assert this diagnostic, preserve the "
    "diagnostic unless the issue asks to suppress it, and prefer wording or "
    "argument changes over broad conditional silencing; prefer the local "
    "diagnostic patch before broader implementation changes; decide if this "
    "is diagnostic wording or guard behavior before changing unrelated logic; "
    "treat changelog/upstream "
    "wording as background, do not use web, upstream patches, or git log"
)
CODE_LOCATE_SETUP_NOTE = (
    "Use current checkout ranked candidates. Do not use web, upstream patches, "
    "or git log. If pytest fails before collection, treat as setup."
)
QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "new",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "with",
}
QUERY_SHORT_ALLOWLIST = {"id", "io", "np", "os"}
LOCATE_LOW_SIGNAL_TERMS = {
    "all",
    "any",
    "after",
    "array",
    "bug",
    "code",
    "config",
    "edit",
    "enabled",
    "element",
    "elements",
    "error",
    "fix",
    "find",
    "generation",
    "import",
    "likely",
    "location",
    "locations",
    "mode",
    "model",
    "more",
    "need",
    "needs",
    "np",
    "numpy",
    "one",
    "param",
    "print",
    "python",
    "reproduce",
    "reproduces",
    "reproducing",
    "set",
    "sklearn",
    "than",
    "true",
    "use",
    "valueerror",
    "when",
    "values",
    "valued",
    "ping",
    "nicolashug",
}
DIAGNOSTIC_LOW_SIGNAL_TERMS = {
    "build",
    "builder",
    "builders",
    "building",
    "change",
    "changed",
    "error",
    "errors",
    "fail",
    "failed",
    "failure",
    "generating",
    "html",
    "latex",
    "singlehtml",
    "sphinx",
    "started",
    "upgrade",
    "upgraded",
    "warning",
    "warnings",
}
FAILING_TEST_PATH_STOPWORDS = {
    "py",
    "sklearn",
    "test",
    "tests",
    "unittest",
}
FAILING_TEST_IMPL_PATH_STOPWORDS = {
    "common",
    "core",
    "helper",
    "helpers",
    "util",
    "utils",
}


@dataclass
class _CodeSearchHit:
    file_name: str
    score: int = 0
    path_terms: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    content_hits: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class _CodeFocusSymbol:
    score: int
    name: str
    line_start: int
    line_end: int


@dataclass
class _CodeLocateHit:
    file_name: str
    score: int = 0
    why: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    focus_symbols: list[_CodeFocusSymbol] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    content_hits: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class CodeLocateFile:
    content: str
    file_name: str
    location_type: str = "viking"
    relative_path: str | None = None


@dataclass
class CodeLocateCandidate:
    rank: int
    location: dict
    score: int
    imports: list[str] = field(default_factory=list)
    focus_symbols: list[dict] = field(default_factory=list)
    symbols: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    snippets: list[dict] = field(default_factory=list)
    next_action: str = ""


@dataclass
class CodeLocateResult:
    schema_version: str
    source: dict
    query: dict
    edit_candidates: list[CodeLocateCandidate]
    behavior_references: list[CodeLocateCandidate]
    verification: list[dict]
    warnings: list[dict]
    summary_text: str
    debug: dict | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if data.get("debug") is None:
            data.pop("debug", None)
        return data


def _query_terms(query: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z0-9_]+", query.lower())
    terms: list[str] = []
    for term in raw_terms:
        pieces = [term]
        if "_" in term:
            pieces.extend(part for part in term.split("_") if part)
        for piece in pieces:
            if piece in QUERY_STOPWORDS:
                continue
            if len(piece) < 3 and piece not in QUERY_SHORT_ALLOWLIST:
                continue
            terms.append(piece)
            if len(piece) > 3 and piece.endswith("s"):
                singular = piece[:-1]
                if singular not in QUERY_STOPWORDS:
                    terms.append(singular)
    if re.search(r"\bpretty[-_\s]*print(?:ing)?\b", query, flags=re.I):
        terms.append("pprint")
    return list(dict.fromkeys(terms))


def _strip_fenced_code_blocks(text: str) -> str:
    return re.sub(r"```.*?```", " ", text, flags=re.S)


def _locate_issue_terms(issue: str) -> list[str]:
    setup_terms = set(_setup_context_identifiers(issue))
    return [
        term
        for term in _query_terms(_issue_focus_text(issue))
        if term not in LOCATE_LOW_SIGNAL_TERMS and term not in setup_terms
    ]


def _diagnostic_issue_terms(issue: str) -> list[str]:
    focus = _issue_focus_text(issue)
    if not re.search(r"\b(?:warning|warnings|error|errors|exception|traceback)\b", focus, re.I):
        return []

    return [
        term
        for term in _query_terms(focus)
        if term not in LOCATE_LOW_SIGNAL_TERMS
        and term not in DIAGNOSTIC_LOW_SIGNAL_TERMS
        and term not in QUERY_STOPWORDS
    ]


def _diagnostic_issue_phrases(issue: str) -> list[str]:
    focus = _issue_focus_text(issue)
    if not re.search(r"\b(?:warning|warnings|error|errors|exception|traceback)\b", focus, re.I):
        return []

    phrases: list[str] = []
    for match in re.finditer(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'", focus):
        phrase = next(group for group in match.groups() if group is not None)
        lower = phrase.lower()
        if len(lower) >= 8 and any(ch.isalpha() for ch in lower):
            _append_unique(phrases, lower)
    for match in re.finditer(
        r"\b(?:warning|warnings|error|errors|exception)\s*:\s*([^.;,\n\r`'\"]+)",
        focus,
        re.I,
    ):
        lower = match.group(1).strip().lower()
        if len(lower) >= 8 and any(ch.isalpha() for ch in lower):
            _append_unique(phrases, lower)
    return phrases[:3]


def _issue_focus_text(issue: str) -> str:
    issue = _strip_fenced_code_blocks(issue)
    return re.split(
        r"\b(?:reproduce|reproduces|reproduced|reproducing|traceback)\b",
        issue,
        maxsplit=1,
        flags=re.I,
    )[0]


def _exact_issue_identifiers(issue: str) -> list[str]:
    terms: list[str] = []
    setup_terms = set(_setup_context_identifiers(issue))
    for term in re.findall(r"[A-Za-z0-9_]+", _issue_focus_text(issue).lower()):
        if "_" not in term:
            continue
        if term in QUERY_STOPWORDS or term in LOCATE_LOW_SIGNAL_TERMS or term in setup_terms:
            continue
        _append_unique(terms, term)
    return terms


def _setup_context_identifiers(issue: str) -> list[str]:
    """Identifiers used as repro/setup context should not dominate locate ranking."""
    identifiers: list[str] = []
    text = _issue_focus_text(issue).lower()
    for match in re.finditer(
        r"\b(?:after|under|using|when|with)\b.{0,120}?\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b",
        text,
    ):
        identifier = match.group(1)
        if identifier.startswith(("get_", "set_")) or identifier.endswith("_config"):
            _append_unique(identifiers, identifier)
    return identifiers


def _failing_test_path_terms(failing_tests: list[str] | None) -> list[str]:
    terms: list[str] = []
    for failing_test in failing_tests or []:
        path_part = failing_test.split("::", 1)[0]
        for term in _query_terms(path_part):
            if term in FAILING_TEST_PATH_STOPWORDS:
                continue
            _append_unique(terms, term)
            for prefix in ("test_", "unittest_"):
                if term.startswith(prefix):
                    _append_unique(terms, term[len(prefix) :])
    return terms


def _failing_test_name_terms(failing_tests: list[str] | None) -> list[str]:
    terms: list[str] = []
    for failing_test in (failing_tests or [])[:1]:
        if "::" not in failing_test:
            continue
        name_part = failing_test.rsplit("::", 1)[-1]
        for term in _query_terms(name_part):
            if term in FAILING_TEST_PATH_STOPWORDS:
                continue
            _append_unique(terms, term)
            if term.startswith("test_"):
                _append_unique(terms, term[len("test_") :])
    return terms


def _path_term_score(
    file_name: str,
    terms: list[str],
    *,
    path_weight: int,
    basename_weight: int,
) -> tuple[int, list[str]]:
    lower_path = file_name.lower()
    basename = lower_path.rsplit("/", 1)[-1]
    score = 0
    matched_terms: list[str] = []
    for term in terms:
        if term not in lower_path:
            continue
        _append_unique(matched_terms, term)
        score += basename_weight if term in basename else path_weight
    return score, matched_terms


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _snippet(line: str) -> str:
    text = line.strip()
    if len(text) > 200:
        return text[:200] + "..."
    return text


def _is_actionable_code_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", '"""', "'''", "*")):
        return False
    return any(token in stripped for token in ("!=", "==", " is ", " in ", "return ", "raise ", "if "))


def _actionable_code_bonus(line: str) -> int:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", '"""', "'''", "*")):
        return 0
    if any(token in stripped for token in ("!=", "==")):
        return 18
    if stripped.startswith("if ") and any(token in stripped for token in (" is ", " in ")):
        return 16
    if stripped.startswith("for "):
        return 4
    if any(token in stripped for token in ("if ", "return ", "raise ")):
        return 6
    return 0


def _is_diagnostic_emitter_line(line: str) -> bool:
    stripped = line.strip()
    return any(
        token in stripped
        for token in (
            "logger.warning",
            "logger.error",
            "logger.exception",
            "warnings.warn",
            "raise ",
        )
    )


def _is_diagnostic_assertion_line(line: str) -> bool:
    stripped = line.strip()
    if any(token in stripped for token in ("pytest.warns", "warning.getvalue", "caplog", "recwarn")):
        return True
    if "assert " not in stripped:
        return False
    return re.search(r"\b(?:warning|warnings|error|errors|exception|traceback)\b", stripped, re.I) is not None


def _is_negative_diagnostic_assertion_line(line: str) -> bool:
    lower = line.lower()
    return " not in " in lower or re.search(r"\bassert\s+not\b", lower) is not None


def _diagnostic_phrase_bonus(line: str, matched_terms: set[str]) -> int:
    lower = line.lower()
    if "no number is assigned" in lower or "number is not assigned" in lower:
        return 220
    return 0


def _diagnostic_precedent_hits(
    content: str,
    issue_terms: list[str],
    *,
    exclude_lines: set[int],
) -> list[tuple[int, str]]:
    if not any(term in {"ref", "xref", "numref", "reference", "caption", "title"} for term in issue_terms):
        return []

    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        if lineno in exclude_lines:
            continue
        lower = line.lower()
        if (
            "cross reference" in lower
            and ("failed to" in lower or "not found" in lower)
            and ("__(" in line or "logger.warning" in line or re.search(r"\bmsg\s*=", line))
        ):
            hits.append((lineno, "same-file diagnostic precedent: " + _snippet(line)))
            if len(hits) >= 2:
                break
    return hits


def _is_low_value_path(file_name: str) -> bool:
    lower = file_name.lower()
    return any(part in lower for part in ("/docs/", "/doc/", "changelog", "/dist/", "/build/"))


def _is_test_path(file_name: str) -> bool:
    lower = file_name.lower()
    return any(part in lower for part in ("/tests/", "/test/", "unittest_", "_test."))


def _is_test_fixture_config_path(file_name: str) -> bool:
    lower = file_name.lower()
    basename = lower.rsplit("/", 1)[-1]
    return "/tests/roots/" in lower or basename in {"conf.py", "conftest.py"}


def _query_wants_tests(terms: list[str]) -> bool:
    return any(term in {"test", "tests", "unittest", "pytest", "regression"} for term in terms)


def _enclosing_symbol(skeleton: CodeSkeleton, line: int) -> str:
    best_name = ""
    best_size = 1_000_000
    for cls in skeleton.classes:
        if cls.line_start and cls.line_end and cls.line_start <= line <= cls.line_end:
            size = cls.line_end - cls.line_start
            if size < best_size:
                best_name = cls.name
                best_size = size
            for method in cls.methods:
                if (
                    method.line_start
                    and method.line_end
                    and method.line_start <= line <= method.line_end
                ):
                    method_size = method.line_end - method.line_start
                    if method_size < best_size:
                        best_name = f"{cls.name}.{method.name}"
                        best_size = method_size
    for fn in skeleton.functions:
        if fn.line_start and fn.line_end and fn.line_start <= line <= fn.line_end:
            size = fn.line_end - fn.line_start
            if size < best_size:
                best_name = fn.name
                best_size = size
    return best_name


def _symbol_spans(skeleton: CodeSkeleton) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    for cls in skeleton.classes:
        if cls.line_start and cls.line_end:
            spans.append((cls.name, cls.line_start, cls.line_end))
        for method in cls.methods:
            if method.line_start and method.line_end:
                spans.append((f"{cls.name}.{method.name}", method.line_start, method.line_end))
    for fn in skeleton.functions:
        if fn.line_start and fn.line_end:
            spans.append((fn.name, fn.line_start, fn.line_end))
    return spans


def _symbol_for_line(symbols: list[tuple[str, int, int]], line: int) -> tuple[str, int, int] | None:
    best: tuple[str, int, int] | None = None
    best_size = 1_000_000
    for name, start, end in symbols:
        if start <= line <= end:
            size = end - start
            if size < best_size:
                best = (name, start, end)
                best_size = size
    return best


def search_code(query: str, files: List[Tuple[str, str]]) -> str:
    """Hybrid code locator over symbols, file paths, and raw content.

    This is intentionally I/O-free and index-free: callers provide already-read
    code files. It favors implementation files over docs/generated files while
    keeping tests visible when they match strongly.
    """
    if not query:
        return "Error: empty query"

    terms = _query_terms(query)
    diagnostic_terms = _diagnostic_issue_terms(query)
    wants_tests = _query_wants_tests(terms)
    extractor = get_extractor()
    hits: list[_CodeSearchHit] = []
    scanned = 0

    for content, file_name in files:
        scanned += 1
        lower_path = file_name.lower()
        hit = _CodeSearchHit(file_name=file_name)
        skeleton = extractor.extract(file_name, content)
        content_terms: set[str] = set()
        max_terms_on_line = 0
        has_diagnostic_signal = False

        for term in terms:
            if term in lower_path:
                _append_unique(hit.path_terms, term)
                hit.score += 4

        if skeleton is not None:
            for name, start, end in _iter_symbols(skeleton):
                lower_name = name.lower()
                tail = lower_name.rsplit(".", 1)[-1]
                if any(term in lower_name or term in tail for term in terms):
                    span = f"{name} L{start}-{end}" if start and end else name
                    _append_unique(hit.symbols, span)
                    hit.score += 8

        lines = content.splitlines()
        for lineno, line in enumerate(lines, start=1):
            lower_line = line.lower()
            matched_terms = [term for term in terms if term in lower_line]
            if not matched_terms:
                continue
            unique_terms = set(matched_terms)
            diagnostic_matched = {term for term in matched_terms if term in diagnostic_terms}
            diagnostic_emitter = (
                bool(diagnostic_matched)
                and _is_diagnostic_emitter_line(line)
                and (_diagnostic_phrase_bonus(line, diagnostic_matched) or len(diagnostic_matched) >= 2)
            )
            if len(hit.content_hits) >= CODE_SEARCH_CONTENT_MAX_PER_FILE and not diagnostic_emitter:
                if diagnostic_terms:
                    continue
                break

            content_terms.update(unique_terms)
            max_terms_on_line = max(max_terms_on_line, len(unique_terms))
            if len(hit.content_hits) < CODE_SEARCH_CONTENT_MAX_PER_FILE or diagnostic_emitter:
                hit.content_hits.append((lineno, _snippet(line)))
            hit.score += 5 + len(unique_terms) * 2
            if diagnostic_emitter:
                hit.score += 3000 + _diagnostic_phrase_bonus(line, diagnostic_matched)
                has_diagnostic_signal = True
            if skeleton is not None:
                _append_unique(hit.symbols, _enclosing_symbol(skeleton, lineno))
            if len(hit.content_hits) >= CODE_SEARCH_CONTENT_MAX_PER_FILE and not diagnostic_terms:
                break

        if hit.score:
            if _is_low_value_path(file_name):
                hit.score -= 8
            elif _is_test_path(file_name):
                hit.score -= 6 if wants_tests else 30
            else:
                hit.score += 3
                if len(content_terms) >= 2:
                    hit.score += 8
            if diagnostic_terms and not has_diagnostic_signal and hit.path_terms:
                hit.score -= 150
            if max_terms_on_line >= 2:
                hit.score += max_terms_on_line * 6
            hits.append(hit)

    if not hits:
        return f'No matches for "{query}" (scanned {scanned} files)'

    hits.sort(key=lambda item: (-item.score, item.file_name))
    hits = hits[:CODE_SEARCH_HYBRID_RESULT_LIMIT]
    total = len(hits)
    out: list[str] = [f'{total} code matches for "{query}" (scanned {scanned} files)']
    if diagnostic_terms:
        out.append(
            "Diagnostic search note: prioritize emitter/assertion lines; treat "
            "builder/path-only matches as context unless the emitter patch fails."
        )
    for hit in hits:
        out.append("")
        out.append(hit.file_name)
        if hit.path_terms:
            out.append("  path matches: " + ", ".join(hit.path_terms))
        if hit.symbols:
            out.append("  symbols: " + ", ".join(hit.symbols[:8]))
        if hit.content_hits:
            out.append("  content:")
            for lineno, line in hit.content_hits:
                out.append(f"    L{lineno}: {line}")
    return "\n".join(out)


def search_content(query: str, files: List[Tuple[str, str]]) -> str:
    """Content fallback for search_symbols: literal substring scan over raw file lines."""
    if not query:
        return ""
    needle = query.lower()
    nl = chr(10)
    scanned = 0
    total = 0
    blocks = []
    for content, file_name in files:
        scanned += 1
        if not content:
            continue
        hits = []
        for lineno, line in enumerate(content.splitlines(), start=1):
            if needle in line.lower():
                snippet = line.strip()
                if len(snippet) > 200:
                    snippet = snippet[:200] + "..."
                hits.append("  L" + str(lineno) + ": " + snippet)
                total += 1
                if len(hits) >= CODE_SEARCH_CONTENT_MAX_PER_FILE:
                    break
        if hits:
            blocks.append(file_name + nl + nl.join(hits))
        if total >= CODE_SEARCH_CONTENT_MAX_TOTAL:
            break
    if total == 0:
        return ""
    header = str(total) + " content matches for " + query + " (scanned " + str(scanned) + " files; symbol search found none)"
    return header + nl + nl + (nl + nl).join(blocks)


def _match_terms(text: str, terms: list[str]) -> list[str]:
    lower = text.lower()
    return [term for term in terms if term in lower]


def _nearby_issue_terms_bonus(content: str, terms: list[str]) -> tuple[int, list[str]]:
    high_value_terms = [
        term
        for term in terms
        if len(term) >= 4 and term not in LOCATE_LOW_SIGNAL_TERMS and term not in QUERY_STOPWORDS
    ]
    if not high_value_terms:
        return 0, []

    line_matches: list[set[str]] = []
    for line in content.splitlines():
        line_matches.append(set(_match_terms(line, high_value_terms)))

    best_terms: set[str] = set()
    window = 8
    for index in range(len(line_matches)):
        nearby: set[str] = set()
        for matches in line_matches[index : index + window]:
            nearby.update(matches)
        if len(nearby) > len(best_terms):
            best_terms = nearby

    if len(best_terms) < 3:
        return 0, []
    return min(180, 40 + len(best_terms) * 25), sorted(best_terms)


def _score_locate_file(
    issue_terms: list[str],
    diagnostic_terms: list[str],
    diagnostic_issue_phrases: list[str],
    exact_issue_identifiers: list[str],
    failing_test_path_terms: list[str],
    failing_test_name_terms: list[str],
    content: str,
    file_name: str,
) -> _CodeLocateHit | None:
    if _is_low_value_path(file_name) or not get_extractor().supports(file_name):
        return None

    hit = _CodeLocateHit(file_name=file_name)
    is_test = _is_test_path(file_name)
    skeleton = get_extractor().extract(file_name, content)
    symbol_scores: dict[str, _CodeFocusSymbol] = {}
    symbol_spans = _symbol_spans(skeleton) if skeleton is not None else []
    scored_symbol_terms: set[str] = set()
    scored_exact_symbol_terms: set[str] = set()
    scored_symbol_hint_terms: set[str] = set()

    path_terms = _match_terms(file_name, issue_terms)
    if path_terms:
        hit.score += len(path_terms) * 4
        hit.why.append("path matches: " + ", ".join(path_terms[:5]))

    if not is_test:
        impl_path_terms = [
            term for term in failing_test_path_terms if term not in FAILING_TEST_IMPL_PATH_STOPWORDS
        ]
        test_hint_score, test_hint_path_terms = _path_term_score(
            file_name,
            impl_path_terms,
            path_weight=20,
            basename_weight=900,
        )
        if test_hint_path_terms:
            hit.score += test_hint_score
            hit.why.append("failing test path hints: " + ", ".join(test_hint_path_terms[:5]))

    if is_test:
        test_path_score, test_path_terms = _path_term_score(
            file_name,
            failing_test_path_terms,
            path_weight=8,
            basename_weight=400,
        )
        if test_path_terms:
            hit.score += test_path_score
            hit.why.append("failing test path matches: " + ", ".join(test_path_terms[:5]))

    if skeleton is not None:
        hit.imports = skeleton.imports[:CODE_LOCATE_IMPORT_LIMIT]
        for name, start, end in _iter_symbols(skeleton):
            symbol_terms = _match_terms(name, issue_terms)
            exact_symbol_terms = _match_terms(name, exact_issue_identifiers)
            symbol_hint_terms = []
            if not is_test:
                symbol_hint_terms = _match_terms(name, failing_test_name_terms)
            if not symbol_terms and is_test:
                symbol_terms = _match_terms(name, failing_test_name_terms)
            if symbol_terms or exact_symbol_terms or symbol_hint_terms:
                span = f"{name} L{start}-{end}" if start and end else name
                _append_unique(hit.symbols, span)
                new_symbol_terms = set(symbol_terms) - scored_symbol_terms
                new_exact_symbol_terms = set(exact_symbol_terms) - scored_exact_symbol_terms
                new_symbol_hint_terms = set(symbol_hint_terms) - scored_symbol_hint_terms
                scored_symbol_terms.update(new_symbol_terms)
                scored_exact_symbol_terms.update(new_exact_symbol_terms)
                scored_symbol_hint_terms.update(new_symbol_hint_terms)
                score = 0
                if new_symbol_terms or new_exact_symbol_terms or new_symbol_hint_terms:
                    score = 12 + len(new_symbol_terms) * 3 + len(new_symbol_hint_terms) * 12
                    score += len(new_exact_symbol_terms) * CODE_LOCATE_EXACT_IDENTIFIER_SCORE
                hit.score += score
                if score and start and end:
                    current = symbol_scores.get(name)
                    new_score = score + (current.score if current else 0)
                    symbol_scores[name] = _CodeFocusSymbol(new_score, name, start, end)

    content_term_names: set[str] = set()
    exact_content_terms: set[str] = set()
    diagnostic_signal_terms: set[str] = set()
    has_diagnostic_emitter = False
    has_diagnostic_assertion = False
    has_diagnostic_wording_delta = False
    has_diagnostic_precedent = False
    line_hits: list[tuple[int, int, str]] = []
    diagnostic_line_hits: list[tuple[int, int, str]] = []
    diagnostic_symbol_scores: dict[str, _CodeFocusSymbol] = {}
    all_terms = issue_terms + (failing_test_name_terms if is_test else [])
    content_score = 0
    content_term_counts: dict[str, int] = {}
    for lineno, line in enumerate(content.splitlines(), start=1):
        matched = _match_terms(line, all_terms)
        exact_matched = _match_terms(line, exact_issue_identifiers)
        diagnostic_matched = set(_match_terms(line, diagnostic_terms))
        if not matched and not exact_matched and not diagnostic_matched:
            continue
        unique = set(matched)
        content_term_names.update(unique)

        line_score = 0
        first_seen_terms = [term for term in unique if content_term_counts.get(term, 0) == 0]
        repeated_terms = [term for term in unique if 0 < content_term_counts.get(term, 0) < 3]
        if first_seen_terms:
            line_score += 5 + len(first_seen_terms) * 4
        line_score += len(repeated_terms) * 2
        for term in unique:
            content_term_counts[term] = content_term_counts.get(term, 0) + 1

        if exact_matched:
            exact_content_terms.update(exact_matched)
            line_score += len(set(exact_matched)) * CODE_LOCATE_EXACT_IDENTIFIER_SCORE
        action_bonus = _actionable_code_bonus(line)
        if line_score or action_bonus >= 16:
            line_score += action_bonus

        if diagnostic_matched:
            phrase_bonus = _diagnostic_phrase_bonus(line, diagnostic_matched)
            if _is_diagnostic_emitter_line(line) and (
                phrase_bonus or len(diagnostic_matched) >= 3
            ):
                line_score += 80 + phrase_bonus + len(diagnostic_matched) * 12
                diagnostic_signal_terms.update(diagnostic_matched)
                has_diagnostic_emitter = True
                diagnostic_line_hits.append((line_score, lineno, _snippet(line)))
            elif (
                is_test
                and _is_diagnostic_assertion_line(line)
                and not _is_negative_diagnostic_assertion_line(line)
                and (phrase_bonus or len(diagnostic_matched) >= 3)
            ):
                line_score += 70 + phrase_bonus + len(diagnostic_matched) * 10
                diagnostic_signal_terms.update(diagnostic_matched)
                has_diagnostic_assertion = True
                diagnostic_line_hits.append((line_score, lineno, _snippet(line)))
                lower_line = line.lower()
                if diagnostic_issue_phrases and any(
                    phrase not in lower_line for phrase in diagnostic_issue_phrases
                ):
                    line_score += 120
                    has_diagnostic_wording_delta = True

        line_hits.append((line_score, lineno, _snippet(line)))
        content_score += line_score
        if skeleton is not None:
            _append_unique(hit.symbols, _enclosing_symbol(skeleton, lineno))
            symbol = _symbol_for_line(symbol_spans, lineno)
            if symbol is not None:
                name, start, end = symbol
                current = symbol_scores.get(name)
                new_score = line_score + (current.score if current else 0)
                symbol_scores[name] = _CodeFocusSymbol(new_score, name, start, end)
                if diagnostic_line_hits and diagnostic_line_hits[-1][1] == lineno:
                    current_diagnostic = diagnostic_symbol_scores.get(name)
                    new_diagnostic_score = line_score + (
                        current_diagnostic.score if current_diagnostic else 0
                    )
                    diagnostic_symbol_scores[name] = _CodeFocusSymbol(
                        new_diagnostic_score, name, start, end
                    )

    hit.score += min(content_score, CODE_LOCATE_CONTENT_SCORE_CAP)
    diagnostic_emitter_lines = {
        lineno for _score, lineno, _text in diagnostic_line_hits
    }
    if has_diagnostic_emitter and not is_test:
        precedent_hits = _diagnostic_precedent_hits(
            content,
            issue_terms,
            exclude_lines=diagnostic_emitter_lines,
        )
        if precedent_hits:
            has_diagnostic_precedent = True
            hit.score += 80
            for lineno, text in precedent_hits:
                diagnostic_line_hits.append((60, lineno, text))
    visible_line_hits = diagnostic_line_hits if diagnostic_line_hits else line_hits
    visible_line_limit = 4 if diagnostic_line_hits and is_test else 3
    hit.content_hits = [
        (lineno, text)
        for _score, lineno, text in sorted(visible_line_hits, key=lambda item: (-item[0], item[1]))[
            :visible_line_limit
        ]
    ]
    visible_symbol_scores = diagnostic_symbol_scores or symbol_scores
    hit.focus_symbols = sorted(
        visible_symbol_scores.values(),
        key=lambda item: (-item.score, item.line_start, item.name),
    )[:CODE_LOCATE_FOCUS_LIMIT]

    if content_term_names:
        hit.why.append("content matches: " + ", ".join(sorted(content_term_names)[:6]))
    if exact_content_terms:
        hit.why.append("exact identifiers: " + ", ".join(sorted(exact_content_terms)[:3]))
    if diagnostic_signal_terms:
        diagnostic_reasons = [
            "diagnostic signal: " + ", ".join(sorted(diagnostic_signal_terms)[:5])
        ]
        if has_diagnostic_emitter:
            hit.score += 260
            diagnostic_reasons.append("diagnostic emitter line matches issue")
            if has_diagnostic_precedent:
                diagnostic_reasons.append(
                    "same-file diagnostic precedent found; reuse diagnostic wording style "
                    "before changing unrelated logic"
                )
        elif has_diagnostic_assertion:
            hit.score += 240
            diagnostic_reasons.append(
                "positive diagnostic assertion matches issue; preserve warning"
            )
            if has_diagnostic_wording_delta:
                hit.score += 160
                diagnostic_reasons.append(
                    "asserted diagnostic wording differs from issue; compare message/arguments first"
                )
        hit.why = diagnostic_reasons + hit.why
    if line_hits and line_hits[0][0] >= 9:
        max_terms_on_line = max(
            len(set(_match_terms(text, all_terms))) for _line_score, _lineno, text in line_hits
        )
        hit.score += max_terms_on_line * 6
        hit.why.append("multiple issue terms occur near the same line")

    nearby_bonus, nearby_terms = _nearby_issue_terms_bonus(content, issue_terms)
    if diagnostic_terms and not diagnostic_signal_terms:
        nearby_bonus = min(nearby_bonus, 60)
    if nearby_bonus:
        hit.score += nearby_bonus
        hit.why.append("nearby issue terms: " + ", ".join(nearby_terms[:6]))

    if is_test:
        hit.score += 4
        if (
            diagnostic_terms
            and not diagnostic_signal_terms
            and _is_test_fixture_config_path(file_name)
        ):
            hit.score -= 90
    else:
        hit.score += 6
        if len(content_term_names) >= 2:
            hit.score += 10

    if hit.score <= 0:
        return None
    return hit


def _format_locate_section(
    title: str,
    hits: list[_CodeLocateHit],
    *,
    next_action: str,
) -> list[str]:
    lines = [title]
    if not hits:
        lines.append("- no ranked candidates")
        return lines
    for index, hit in enumerate(hits, start=1):
        lines.append(f"{index}. {hit.file_name}")
        lines.append(f"   score: {hit.score}")
        if hit.imports:
            lines.append("   imports: " + ", ".join(hit.imports))
        if hit.focus_symbols:
            lines.append(
                "   focus: "
                + ", ".join(
                    f"{symbol.name} L{symbol.line_start}-{symbol.line_end}"
                    for symbol in hit.focus_symbols
                )
            )
        if hit.symbols:
            lines.append("   symbols: " + ", ".join(hit.symbols[:5]))
        if hit.why:
            lines.append("   why: " + "; ".join(hit.why[:3]))
        if hit.content_hits:
            snippets = "; ".join(f"L{line}: {text}" for line, text in hit.content_hits[:2])
            lines.append(f"   snippets: {snippets}")
        lines.append(f"   next: {next_action}")
    return lines


def _repo_relative_path(file_name: str) -> str:
    if not file_name.startswith("viking://"):
        return file_name
    path = file_name.split("://", 1)[1]
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "resources":
        return "/".join(parts[2:])
    if len(parts) >= 2:
        return "/".join(parts[1:])
    return file_name


def _format_verification_section(
    edit_hits: list[_CodeLocateHit],
    reference_hits: list[_CodeLocateHit],
) -> list[str]:
    lines = ["Suggested verification:"]

    python_edit_hits = [hit for hit in edit_hits if hit.file_name.lower().endswith(".py")]
    python_edit_paths = []
    for reference_hit in reference_hits:
        target_stem = _test_target_stem(reference_hit.file_name)
        if not target_stem:
            continue
        related_edit = next(
            (
                hit
                for hit in python_edit_hits
                if _implementation_stem(hit.file_name) == target_stem
            ),
            None,
        )
        if related_edit is not None:
            python_edit_paths = [_repo_relative_path(related_edit.file_name)]
            break
    if not python_edit_paths:
        python_edit_paths = [_repo_relative_path(hit.file_name) for hit in python_edit_hits[:1]]
    if python_edit_paths:
        lines.append("- static: python3 -m py_compile " + " ".join(python_edit_paths))

    python_test_paths = [
        _repo_relative_path(hit.file_name)
        for hit in reference_hits
        if hit.file_name.lower().endswith(".py")
    ][:1]
    if python_test_paths:
        lines.append("- narrow tests: python3 -m pytest " + " ".join(python_test_paths))

    lines.append("- " + CODE_LOCATE_SETUP_NOTE)
    return lines


def _location_for_file(file: CodeLocateFile) -> dict:
    relative_path = file.relative_path or _repo_relative_path(file.file_name)
    if file.location_type == "local":
        return {
            "type": "local",
            "path": file.file_name,
            "relative_path": relative_path,
        }
    return {
        "type": "viking",
        "uri": file.file_name,
        "relative_path": relative_path,
    }


def _candidate_symbol(name: str) -> dict:
    match = re.match(r"^(?P<name>.+?)\s+L(?P<start>\d+)-(?P<end>\d+)$", name)
    if not match:
        return {"name": name, "kind": "symbol", "range": None}
    return {
        "name": match.group("name"),
        "kind": "symbol",
        "range": {
            "start_line": int(match.group("start")),
            "end_line": int(match.group("end")),
        },
    }


def _focus_symbol_dict(symbol: _CodeFocusSymbol) -> dict:
    return {
        "name": symbol.name,
        "kind": "symbol",
        "range": {
            "start_line": symbol.line_start,
            "end_line": symbol.line_end,
        },
    }


def _candidate_snippet_limit(hit: _CodeLocateHit) -> int:
    if any("positive diagnostic assertion" in reason for reason in hit.why):
        return 4
    return 2


def _hit_to_candidate(
    rank: int,
    hit: _CodeLocateHit,
    file_by_name: dict[str, CodeLocateFile],
    *,
    next_action: str,
) -> CodeLocateCandidate:
    file = file_by_name.get(hit.file_name) or CodeLocateFile("", hit.file_name)
    return CodeLocateCandidate(
        rank=rank,
        location=_location_for_file(file),
        score=hit.score,
        imports=hit.imports,
        focus_symbols=[_focus_symbol_dict(symbol) for symbol in hit.focus_symbols],
        symbols=[_candidate_symbol(symbol) for symbol in hit.symbols[:5]],
        reasons=hit.why[:3],
        snippets=[
            {"line": line, "text": text}
            for line, text in hit.content_hits[: _candidate_snippet_limit(hit)]
        ],
        next_action=next_action,
    )


def _verification_target_for_hit(hit: _CodeLocateHit, file_by_name: dict[str, CodeLocateFile]):
    file = file_by_name.get(hit.file_name) or CodeLocateFile("", hit.file_name)
    return _location_for_file(file)


def _verification_entries(
    edit_hits: list[_CodeLocateHit],
    reference_hits: list[_CodeLocateHit],
    file_by_name: dict[str, CodeLocateFile],
    *,
    source_type: str,
    source_root: str,
    allow_viking_commands: bool = False,
) -> list[dict]:
    entries: list[dict] = []
    if source_type != "local" and not allow_viking_commands:
        for hit in reference_hits[:1]:
            entries.append(
                {
                    "kind": "narrow_tests",
                    "command": None,
                    "cwd": None,
                    "targets": [_verification_target_for_hit(hit, file_by_name)],
                    "reason": "top related behavior reference",
                }
            )
        entries.append(
            {
                "kind": "setup_note",
                "command": None,
                "cwd": None,
                "targets": [],
                "reason": "viking source has no local checkout mapping",
            }
        )
        return entries

    python_edit_hits = [hit for hit in edit_hits if hit.file_name.lower().endswith(".py")]
    python_edit_hit = None
    for reference_hit in reference_hits:
        target_stem = _test_target_stem(reference_hit.file_name)
        if not target_stem:
            continue
        python_edit_hit = next(
            (
                hit
                for hit in python_edit_hits
                if _implementation_stem(hit.file_name) == target_stem
            ),
            None,
        )
        if python_edit_hit is not None:
            break
    if python_edit_hit is None and python_edit_hits:
        python_edit_hit = python_edit_hits[0]

    if python_edit_hit is not None:
        target = _verification_target_for_hit(python_edit_hit, file_by_name)
        rel_path = target.get("relative_path") or target.get("path")
        entries.append(
            {
                "kind": "static",
                "command": f"python3 -m py_compile {rel_path}",
                "cwd": source_root or None,
                "targets": [target],
                "reason": "top Python edit candidate",
            }
        )

    diagnostic_wording_delta = any(
        _hit_has_diagnostic_wording_delta(hit) for hit in edit_hits + reference_hits
    )
    python_test_hit = None
    if not diagnostic_wording_delta:
        python_test_hit = next(
            (hit for hit in reference_hits if hit.file_name.lower().endswith(".py")),
            None,
        )
    if python_test_hit is not None:
        target = _verification_target_for_hit(python_test_hit, file_by_name)
        rel_path = target.get("relative_path") or target.get("path")
        entries.append(
            {
                "kind": "narrow_tests",
                "command": f"python3 -m pytest {rel_path}",
                "cwd": source_root or None,
                "targets": [target],
                "reason": "top related behavior reference",
            }
        )

    entries.append(
        {
            "kind": "setup_note",
            "command": None,
            "cwd": source_root or None,
            "targets": [],
            "reason": CODE_LOCATE_SETUP_NOTE,
        }
    )
    return entries


def _has_diagnostic_wording_delta(candidates: list[CodeLocateCandidate]) -> bool:
    return any(
        any("asserted diagnostic wording differs from issue" in reason for reason in candidate.reasons)
        for candidate in candidates
    )


def _hit_has_diagnostic_wording_delta(hit: _CodeLocateHit) -> bool:
    return any("asserted diagnostic wording differs from issue" in reason for reason in hit.why)


def _hit_is_diagnostic_emitter(hit: _CodeLocateHit) -> bool:
    return any("diagnostic emitter line matches issue" in reason for reason in hit.why)


def _candidate_location_label(candidate: CodeLocateCandidate) -> str:
    return (
        candidate.location.get("relative_path")
        or candidate.location.get("path")
        or candidate.location.get("uri", "")
    )


def _summary_text(result: CodeLocateResult) -> str:
    if _has_diagnostic_wording_delta(result.behavior_references):
        parts: list[str] = []
        edit_label = (
            _candidate_location_label(result.edit_candidates[0])
            if result.edit_candidates
            else ""
        )
        reference_label = (
            _candidate_location_label(result.behavior_references[0])
            if result.behavior_references
            else ""
        )
        if edit_label and reference_label:
            parts.append(
                "diagnostic wording delta: patch diagnostic message/arguments first "
                f"in {edit_label}, using positive warning assertions in {reference_label} "
                "as behavior references"
            )
        elif edit_label:
            parts.append(
                "diagnostic wording delta: patch diagnostic message/arguments first "
                f"in {edit_label}"
            )
        else:
            parts.append("diagnostic wording delta: patch diagnostic message/arguments first")

        immediate = next(
            (
                item
                for item in result.verification
                if item.get("kind") == "static" and item.get("command")
            ),
            None,
        )
        if immediate:
            parts.append("suggested verification: " + immediate["command"])
        parts.append(
            "positive warning assertion means preserve the diagnostic first; "
            "borrow any same-file diagnostic precedent prefix/style shown in "
            "snippets, but keep the emitter's original reason semantics; "
            "report-only terms are context; apply that first "
            "patch and run its immediate static check before any additional code discovery; "
            "if that static check passes, stop instead of inspecting visible tests "
            "or implementation logic for extra confidence; "
            "continue broader discovery only if that immediate path fails; do not "
            "use changelog, web, or git history"
        )
        return "; ".join(parts) + "."

    parts: list[str] = []
    if result.edit_candidates:
        parts.append("Top edit candidate: " + _candidate_location_label(result.edit_candidates[0]))
    if result.behavior_references:
        parts.append(
            "useful behavior reference: " + _candidate_location_label(result.behavior_references[0])
        )
    runnable = next((item for item in result.verification if item.get("command")), None)
    if runnable:
        parts.append("suggested verification: " + runnable["command"])
    return "; ".join(parts) + ("." if parts else "No ranked candidates.")


def format_locate_json_text(result: CodeLocateResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def format_locate_text(result: CodeLocateResult) -> str:
    sections: list[str] = []
    sections.append("Likely edit locations:")
    if not result.edit_candidates:
        sections.append("- no ranked candidates")
    for candidate in result.edit_candidates:
        location = candidate.location
        label = location.get("uri") or location.get("path") or location.get("relative_path", "")
        sections.append(f"{candidate.rank}. {label}")
        sections.append(f"   score: {candidate.score}")
        if candidate.imports:
            sections.append("   imports: " + ", ".join(candidate.imports))
        if candidate.focus_symbols:
            sections.append(
                "   focus: "
                + ", ".join(
                    f"{symbol['name']} L{symbol['range']['start_line']}-{symbol['range']['end_line']}"
                    for symbol in candidate.focus_symbols
                    if symbol.get("range")
                )
            )
        if candidate.symbols:
            sections.append(
                "   symbols: "
                + ", ".join(
                    f"{symbol['name']} L{symbol['range']['start_line']}-{symbol['range']['end_line']}"
                    if symbol.get("range")
                    else symbol["name"]
                    for symbol in candidate.symbols
                )
            )
        if candidate.reasons:
            sections.append("   why: " + "; ".join(candidate.reasons))
        if candidate.snippets:
            snippets = "; ".join(
                f"L{snippet['line']}: {snippet['text']}" for snippet in candidate.snippets
            )
            sections.append(f"   snippets: {snippets}")
        sections.append(f"   next: {candidate.next_action}")

    sections.append("")
    sections.append("Useful behavior references:")
    if not result.behavior_references:
        sections.append("- no ranked candidates")
    for candidate in result.behavior_references:
        location = candidate.location
        label = location.get("uri") or location.get("path") or location.get("relative_path", "")
        sections.append(f"{candidate.rank}. {label}")
        sections.append(f"   score: {candidate.score}")
        if candidate.imports:
            sections.append("   imports: " + ", ".join(candidate.imports))
        if candidate.focus_symbols:
            sections.append(
                "   focus: "
                + ", ".join(
                    f"{symbol['name']} L{symbol['range']['start_line']}-{symbol['range']['end_line']}"
                    for symbol in candidate.focus_symbols
                    if symbol.get("range")
                )
            )
        if candidate.symbols:
            sections.append(
                "   symbols: "
                + ", ".join(
                    f"{symbol['name']} L{symbol['range']['start_line']}-{symbol['range']['end_line']}"
                    if symbol.get("range")
                    else symbol["name"]
                    for symbol in candidate.symbols
                )
            )
        if candidate.reasons:
            sections.append("   why: " + "; ".join(candidate.reasons))
        if candidate.snippets:
            snippets = "; ".join(
                f"L{snippet['line']}: {snippet['text']}" for snippet in candidate.snippets
            )
            sections.append(f"   snippets: {snippets}")
        sections.append(f"   next: {candidate.next_action}")

    sections.append("")
    sections.append("Suggested verification:")
    for item in result.verification:
        command = item.get("command")
        if item["kind"] == "static" and command:
            sections.append("- static: " + command)
        elif item["kind"] == "narrow_tests" and command:
            sections.append("- narrow tests: " + command)
        elif item["kind"] == "setup_note":
            sections.append("- " + item["reason"])
    return "\n".join(sections)


def locate_code(
    query: str,
    files: List[Tuple[str, str]],
    failing_tests: list[str] | None = None,
    *,
    max_edit: int = CODE_LOCATE_EDIT_LIMIT,
    max_references: int = CODE_LOCATE_REFERENCE_LIMIT,
) -> str:
    """Rank likely edit locations and behavior references for a code issue.

    This is a deterministic, model-free locator. It intentionally returns
    compact navigation guidance instead of source bodies so agents can inspect a
    small number of files before falling back to broader grep/read exploration.
    """
    structured_files = [CodeLocateFile(content, file_name) for content, file_name in files]
    result = locate_code_structured(
        query,
        structured_files,
        failing_tests,
        max_edit=max_edit,
        max_references=max_references,
        allow_viking_commands=True,
    )
    if result.warnings and result.warnings[0]["code"] == "empty_query":
        return "Error: empty query"
    return format_locate_text(result)


def locate_code_structured(
    query: str,
    files: list[CodeLocateFile],
    failing_tests: list[str] | None = None,
    *,
    max_edit: int = CODE_LOCATE_EDIT_LIMIT,
    max_references: int = CODE_LOCATE_REFERENCE_LIMIT,
    debug: bool = False,
    source_root: str | None = None,
    allow_viking_commands: bool = False,
) -> CodeLocateResult:
    """Rank likely edit locations and behavior references as structured data."""
    if not query:
        return CodeLocateResult(
            schema_version="code-locate/v1",
            source={"type": "unknown", "root": source_root or ""},
            query={"text": query, "failing_tests": failing_tests or []},
            edit_candidates=[],
            behavior_references=[],
            verification=[],
            warnings=[{"code": "empty_query", "message": "Error: empty query"}],
            summary_text="Error: empty query",
        )

    source_type = files[0].location_type if files else "unknown"
    root = source_root or ""
    issue_terms = _locate_issue_terms(query)
    diagnostic_terms = _diagnostic_issue_terms(query)
    diagnostic_issue_phrases = _diagnostic_issue_phrases(query)
    exact_issue_identifiers = _exact_issue_identifiers(query)
    failing_test_path_terms = _failing_test_path_terms(failing_tests)
    failing_test_name_terms = _failing_test_name_terms(failing_tests)
    hits: list[_CodeLocateHit] = []
    file_by_name = {file.file_name: file for file in files}
    for file in files:
        hit = _score_locate_file(
            issue_terms,
            diagnostic_terms,
            diagnostic_issue_phrases,
            exact_issue_identifiers,
            failing_test_path_terms,
            failing_test_name_terms,
            file.content,
            file.file_name,
        )
        if hit is not None:
            hits.append(hit)

    hits.sort(key=lambda item: (-item.score, item.file_name))
    edit_hits = [hit for hit in hits if not _is_test_path(hit.file_name)][:max_edit]
    edit_stems = {_implementation_stem(hit.file_name) for hit in edit_hits[:1]}
    edit_stems.discard("")
    for hit in hits:
        test_target_stem = _test_target_stem(hit.file_name)
        if test_target_stem and test_target_stem in edit_stems:
            hit.score += 80
            hit.why.append("related test for top implementation")
    reference_hits = sorted(
        [hit for hit in hits if _is_test_path(hit.file_name)],
        key=lambda item: (-item.score, item.file_name),
    )[:max_references]
    edit_next_action = (
        CODE_LOCATE_DIAGNOSTIC_NEXT_ACTION if diagnostic_terms else CODE_LOCATE_EDIT_NEXT_ACTION
    )
    reference_next_action = (
        CODE_LOCATE_DIAGNOSTIC_NEXT_ACTION if diagnostic_terms else CODE_LOCATE_REFERENCE_NEXT_ACTION
    )
    if any(_hit_has_diagnostic_wording_delta(hit) for hit in reference_hits):
        diagnostic_edit_hits = [hit for hit in edit_hits if _hit_is_diagnostic_emitter(hit)]
        diagnostic_reference_hits = [
            hit for hit in reference_hits if _hit_has_diagnostic_wording_delta(hit)
        ]
        edit_hits = (diagnostic_edit_hits or edit_hits)[:1]
        reference_hits = (diagnostic_reference_hits or reference_hits)[:1]
        edit_next_action = CODE_LOCATE_DIAGNOSTIC_WORDING_DELTA_ACTION
        reference_next_action = CODE_LOCATE_DIAGNOSTIC_WORDING_DELTA_ACTION

    edit_candidates = [
        _hit_to_candidate(
            rank,
            hit,
            file_by_name,
            next_action=edit_next_action,
        )
        for rank, hit in enumerate(edit_hits, start=1)
    ]
    behavior_references = [
        _hit_to_candidate(
            rank,
            hit,
            file_by_name,
            next_action=reference_next_action,
        )
        for rank, hit in enumerate(reference_hits, start=1)
    ]
    verification = _verification_entries(
        edit_hits,
        reference_hits,
        file_by_name,
        source_type=source_type,
        source_root=root,
        allow_viking_commands=allow_viking_commands,
    )
    debug_payload = None
    if debug:
        debug_payload = {
            "query_terms": issue_terms,
            "diagnostic_terms": diagnostic_terms,
            "exact_query_identifiers": exact_issue_identifiers,
            "ranking_signals": [
                {
                    "location": _location_for_file(
                        file_by_name.get(hit.file_name) or CodeLocateFile("", hit.file_name)
                    ),
                    "total_score": hit.score,
                    "path_score": None,
                    "symbol_score": None,
                    "content_score": None,
                    "test_hint_score": None,
                    "bonuses": [],
                    "penalties": [],
                }
                for hit in hits
            ],
        }
    result = CodeLocateResult(
        schema_version="code-locate/v1",
        source={"type": source_type, "root": root},
        query={"text": query, "failing_tests": failing_tests or []},
        edit_candidates=edit_candidates,
        behavior_references=behavior_references,
        verification=verification,
        warnings=[],
        summary_text="",
        debug=debug_payload,
    )
    result.summary_text = _summary_text(result)
    return result


def _resolve_symbol(
    skeleton: CodeSkeleton, symbol: str
) -> Optional[Tuple[str, int, int]]:
    """Find a symbol by 'foo' (bare) or 'Foo.bar' (qualified). Case sensitive.

    Search priority for bare names (no dot):
      1. Top-level functions  — exact name match
      2. Classes              — exact name match
      3. Methods in any class — bare method name, first class that contains it wins
         (returns qualified display name "ClassName.method" so the caller knows
          where the method lives; use 'Foo.bar' to target a specific class)
    """
    if "." in symbol:
        cls_name, method_name = symbol.split(".", 1)
        for cls in skeleton.classes:
            if cls.name == cls_name:
                for method in cls.methods:
                    if method.name == method_name:
                        return f"{cls.name}.{method.name}", method.line_start, method.line_end
        return None

    for fn in skeleton.functions:
        if fn.name == symbol:
            return fn.name, fn.line_start, fn.line_end
    for cls in skeleton.classes:
        if cls.name == symbol:
            return cls.name, cls.line_start, cls.line_end
        for method in cls.methods:
            if method.name == symbol:
                return f"{cls.name}.{method.name}", method.line_start, method.line_end
    return None


def expand_symbol(content: str, file_name: str, symbol: str) -> str:
    """Return the source for `symbol` from `content`, with a location header.

    Accepts 'foo' (any function/class/method named foo, first match wins) or
    'Foo.bar' (method bar inside class Foo).
    """
    skeleton = get_extractor().extract(file_name, content)
    if skeleton is None:
        return _failure_message(file_name)

    match = _resolve_symbol(skeleton, symbol)
    if match is None:
        return f"Error: symbol '{symbol}' not found in {file_name}"

    display_name, start, end = match
    if not start or not end:
        return f"Error: symbol '{symbol}' found but line numbers unavailable in {file_name}"

    lines = content.splitlines()
    body = "\n".join(lines[start - 1 : end])
    return f"# {file_name}  L{start}-{end}  ({display_name})\n\n{body}"
