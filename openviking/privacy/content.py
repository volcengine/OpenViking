# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rewrite string values without changing a document's keys or value types."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

import yaml

from openviking.core.namespace import classify_uri
from openviking.core.skill_loader import SkillLoader
from openviking.storage.abstract_overview import is_abstract_overview_uri
from openviking_cli.exceptions import InvalidArgumentError

_JSON_STRING = re.compile(r'("[^"\\]*(?:\\.[^"\\]*)*")(\s*:)?')
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_YAML_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


@dataclass(frozen=True, slots=True)
class ContentString:
    start: int
    end: int
    value: str
    quoted: bool = False


@dataclass(slots=True)
class _YamlAliases:
    content: str
    offset: int
    nodes: list[yaml.Node | None]
    shared: set[int]


class PrivacyContent:
    """Keep original string positions and serialize only changed values."""

    def __init__(
        self,
        content: str,
        uri: str,
        *,
        preserve_skill_metadata: bool = False,
        _structure_only: bool = False,
    ):
        self.content = content
        self.strings: list[ContentString] = []
        self._yaml_structures: list[object] = []
        self._yaml_key_aliases: dict[int, _YamlAliases] = {}
        self._yaml_nodes: list[list[yaml.Node | None]] = []
        self._uri = uri
        self._preserve_skill_metadata = preserve_skill_metadata
        self._structure_only = _structure_only
        skill = bool(uri) and classify_uri(uri).is_skill
        start = 0
        path = uri.lower()
        if path.endswith((".md", ".markdown")):
            frontmatter = SkillLoader.FRONTMATTER_PATTERN.match(content)
            if frontmatter:
                if not is_abstract_overview_uri(uri) and not (skill and preserve_skill_metadata):
                    self._add_yaml(
                        frontmatter.group(1),
                        frontmatter.start(1),
                        skip={"name"} if skill else set(),
                    )
                start = frontmatter.start(2)
        body = content[start:]
        if self.is_skill_metadata(uri):
            if not preserve_skill_metadata:
                self._add_yaml(body, start, skip={"name"})
        elif path.endswith(".json"):
            self._add_json(body, start)
        elif path.endswith(".jsonl"):
            offset = start
            for line in body.split("\n"):
                if line.strip():
                    self._add_json(line, offset)
                offset += len(line) + 1
        elif path.endswith((".yaml", ".yml")):
            self._add_yaml(body, start)
        else:
            self.strings.append(ContentString(start, len(content), body))
        self.strings.sort(key=lambda item: item.start)

    @staticmethod
    def is_skill_metadata(uri: str) -> bool:
        return uri.endswith("/.abstract.md") and classify_uri(uri).is_skill

    def _add_json(self, content: str, offset: int) -> None:
        try:
            json.loads(content)
        except RecursionError:
            raise self._structure_error() from None
        except ValueError:
            self.strings.append(ContentString(offset, offset + len(content), content))
            return
        if self._structure_only:
            return
        for match in _JSON_STRING.finditer(content):
            if match[2]:
                continue
            text = match[1]
            self.strings.append(
                ContentString(
                    offset + match.start(1),
                    offset + match.end(1),
                    json.loads(text) if "\\" in text else text[1:-1],
                    True,
                )
            )

    def _add_yaml(self, content: str, offset: int, *, skip: set[str] | None = None) -> None:
        try:
            nodes = list(yaml.compose_all(content, Loader=_YAML_LOADER))
            if self._structure_only:
                self._yaml_nodes.append(nodes)
                return
            structure = []
            shared: set[int] = set()
            check_aliases = "*" in content
            for node in nodes:
                seen: dict[tuple[yaml.Node, bool], int] | None = {} if check_aliases else None
                structure.append(self._yaml_shape(node, seen))
                if seen:
                    shared.update(
                        offset + current.end_mark.index
                        for current, key in seen
                        if not key
                        and isinstance(current, yaml.ScalarNode)
                        and (current, True) in seen
                    )
            if shared:
                self._yaml_key_aliases[len(self._yaml_structures)] = _YamlAliases(
                    content, offset, nodes, shared
                )
        except RecursionError:
            raise self._structure_error() from None
        except yaml.YAMLError:
            self.strings.append(ContentString(offset, offset + len(content), content))
            return
        self._yaml_structures.append(structure)
        pending = [(node, True) for node in nodes]
        visited: set[int] = set()
        tokens = None
        while pending:
            current, root = pending.pop()
            if current is None or id(current) in visited:
                continue
            visited.add(id(current))
            if isinstance(current, yaml.MappingNode):
                for key, value in current.value:
                    if root and skip and isinstance(key.value, str) and key.value in skip:
                        visited.add(id(value))
                    else:
                        pending.append((value, False))
            elif isinstance(current, yaml.SequenceNode):
                pending.extend((value, False) for value in current.value)
            elif current.tag == "tag:yaml.org,2002:str":
                start, end = current.start_mark.index, current.end_mark.index
                if content[start : start + 1] in {"&", "!"}:
                    # Node marks include tags and anchors; keep those prefixes intact.
                    if tokens is None:
                        tokens = {
                            token.end_mark.index: token
                            for token in yaml.scan(content, Loader=_YAML_LOADER)
                            if isinstance(token, yaml.ScalarToken)
                        }
                    token = tokens.get(end)
                    if token is None:
                        # !!str without a scalar represents an empty string.
                        continue
                    start = token.start_mark.index
                self.strings.append(
                    ContentString(
                        offset + start,
                        offset + end,
                        current.value,
                        True,
                    )
                )

    @staticmethod
    def _yaml_shape(
        node: yaml.Node | None,
        seen: dict[tuple[yaml.Node, bool], int] | None = None,
        *,
        key: bool = False,
    ) -> object:
        if node is None:
            return None
        if seen is None:
            seen = {}
        # An alias first visited as a value must still preserve its content as a key.
        identity = (node, key)
        if identity in seen:
            return ("alias", seen[identity])
        seen[identity] = len(seen)
        if isinstance(node, yaml.MappingNode):
            return (
                node.tag,
                tuple(
                    (
                        PrivacyContent._yaml_shape(name, seen, key=True),
                        PrivacyContent._yaml_shape(value, seen, key=key),
                    )
                    for name, value in node.value
                ),
            )
        if isinstance(node, yaml.SequenceNode):
            return (
                node.tag,
                tuple(PrivacyContent._yaml_shape(value, seen, key=key) for value in node.value),
            )
        return (node.tag, node.value) if key else node.tag

    @staticmethod
    def _rewrite_yaml_aliases(
        document: _YamlAliases, values: dict[int, str]
    ) -> tuple[list[tuple[int, int, str]], set[int]]:
        """Split only changed aliases that cross between keys and values."""
        content, offset = document.content, document.offset
        events = yaml.parse(content, Loader=_YAML_LOADER)
        anchors = set()
        names: dict[yaml.Node, str] = {}
        emitted: dict[tuple[yaml.Node, bool], str] = {}
        edits: list[tuple[int, int, str]] = []
        key_ends: set[int] = set()
        tokens = None
        records = []
        parents: dict[yaml.Node, set[yaml.Node]] = {}
        affected = set()

        def emit(node, key):
            anchor = None
            if node in names:
                identity = (node, key)
                if identity in emitted:
                    yield yaml.AliasEvent(emitted[identity])
                    return
                anchor = names[node] + ("_key" if key else "_value")
                while anchor in anchors:
                    anchor += "_"
                anchors.add(anchor)
                emitted[identity] = anchor
            if isinstance(node, yaml.ScalarNode):
                value = node.value if key else values.get(offset + node.end_mark.index, node.value)
                yield yaml.ScalarEvent(
                    anchor, node.tag, (False, node.tag == "tag:yaml.org,2002:str"), value, style='"'
                )
            elif isinstance(node, yaml.SequenceNode):
                yield yaml.SequenceStartEvent(
                    anchor, node.tag, node.tag == "tag:yaml.org,2002:seq", flow_style=True
                )
                for child in node.value:
                    yield from emit(child, key)
                yield yaml.SequenceEndEvent()
            else:
                yield yaml.MappingStartEvent(
                    anchor, node.tag, node.tag == "tag:yaml.org,2002:map", flow_style=True
                )
                for name, value in node.value:
                    yield from emit(name, True)
                    yield from emit(value, key)
                yield yaml.MappingEndEvent()

        def walk(node, key=False, flow_key=False, parent=None):
            event = next(events)
            if event.anchor:
                records.append((node, key, flow_key, event))
                if parent is not None:
                    parents.setdefault(node, set()).add(parent)
                if isinstance(event, yaml.AliasEvent):
                    return
                anchors.add(event.anchor)
                names[node] = event.anchor
                parent = node
            if node is None:
                return
            if isinstance(node, yaml.ScalarNode):
                end = offset + node.end_mark.index
                if end in values:
                    if parent is not None:
                        affected.add(parent)
                    if key:
                        key_ends.add(end)
            elif isinstance(node, yaml.SequenceNode):
                for child in node.value:
                    walk(child, key, parent=parent)
                next(events)
            else:
                for name, value in node.value:
                    walk(name, True, bool(node.flow_style))
                    walk(value, key, parent=parent)
                next(events)

        next(events)  # Stream start.
        for node in document.nodes:
            next(events)  # Document start.
            walk(node)
            next(events)  # Document end.
        pending = list(affected)
        while pending:
            for parent in parents.get(pending.pop(), ()):
                if parent not in affected:
                    affected.add(parent)
                    pending.append(parent)
        for node, key, flow_key, event in records:
            if not isinstance(event, yaml.AliasEvent):
                emitted[node, key] = event.anchor
                continue
            anchor = emitted.get((node, key))
            if node not in affected or anchor == event.anchor:
                continue
            if anchor is not None:
                value = "*" + anchor
            else:
                parts = [yaml.StreamStartEvent(), yaml.DocumentStartEvent()]
                parts.extend(emit(node, key))
                parts.extend((yaml.DocumentEndEvent(), yaml.StreamEndEvent()))
                value = yaml.emit(
                    parts,
                    Dumper=_YAML_DUMPER,
                    allow_unicode=True,
                    # libyaml uses -1 for unlimited width; Python accepts infinity.
                    width=float("inf") if _YAML_DUMPER is yaml.SafeDumper else -1,
                ).rstrip("\n")
            if key:
                # Expanded or renamed aliases may exceed YAML's simple-key limit.
                if tokens is None:
                    tokens = {}
                    start = alias = None
                    for token in yaml.scan(content, Loader=_YAML_LOADER):
                        if (
                            isinstance(start, yaml.KeyToken)
                            and start.start_mark.index == start.end_mark.index
                            and isinstance(alias, yaml.AliasToken)
                            and isinstance(token, yaml.ValueToken)
                        ):
                            tokens[alias.start_mark.index] = token
                        start, alias = alias, token
                colon = tokens.get(event.start_mark.index)
                if colon is not None:
                    value = "? " + value
                    if not flow_key:
                        position = offset + colon.start_mark.index
                        edits.append((position, position, "\n" + " " * event.start_mark.column))
            edits.append((offset + event.start_mark.index, offset + event.end_mark.index, value))
        return edits, key_ends

    @staticmethod
    def _same_yaml_structure(before: list[yaml.Node | None], after: list[yaml.Node | None]) -> bool:
        """Compare key contents and value types independently of alias sharing."""
        if len(before) != len(after):
            return False
        pending = [(left, right, False) for left, right in zip(before, after, strict=True)]
        visited = set()
        while pending:
            left, right, key = pending.pop()
            identity = (left, right, key)
            if identity in visited:
                continue
            visited.add(identity)
            if left is None or right is None:
                if left is not right:
                    return False
            elif type(left) is not type(right) or left.tag != right.tag:
                return False
            elif isinstance(left, yaml.ScalarNode):
                if key and left.value != right.value:
                    return False
            elif len(left.value) != len(right.value):
                return False
            elif isinstance(left, yaml.SequenceNode):
                pending.extend((a, b, key) for a, b in zip(left.value, right.value, strict=True))
            else:
                for (a, b), (c, d) in zip(left.value, right.value, strict=True):
                    pending.extend(((a, c, True), (b, d, key)))
        return True

    def render(self, replace: Callable[[ContentString], str]) -> str:
        edits: list[tuple[int, int, str]] = []
        values: dict[int, str] = {}
        for item in self.strings:
            value = replace(item)
            if value == item.value:
                continue
            if self._yaml_key_aliases:
                values[item.end] = value
            if item.quoted:
                # JSON quoting is valid YAML too, including multiline and Unicode values.
                value = (
                    json.dumps(value, ensure_ascii=False)
                    .replace("\u0085", r"\u0085")
                    .replace("\u2028", r"\u2028")
                    .replace("\u2029", r"\u2029")
                )
                # Keep one complete string token, including JSONL record boundaries.
                try:
                    if (
                        not value.startswith('"')
                        or not value.endswith('"')
                        or not isinstance(json.loads(value), str)
                    ):
                        raise self._structure_error()
                except (ValueError, RecursionError):
                    raise self._structure_error() from None
                if item.end > item.start and self.content[item.end - 1] == "\n":
                    value += "\n"
            edits.append((item.start, item.end, value))
        if not edits:
            return self.content
        split_documents = set()
        for index, aliases in self._yaml_key_aliases.items():
            if aliases.shared.isdisjoint(values):
                continue
            extra, key_ends = self._rewrite_yaml_aliases(aliases, values)
            edits = [edit for edit in edits if edit[1] not in key_ends] + extra
            split_documents.add(index)
        if split_documents:
            edits.sort()
        chunks: list[str] = []
        cursor = 0
        for start, end, value in edits:
            chunks.extend((self.content[cursor:start], value))
            cursor = end
        chunks.append(self.content[cursor:])
        result = "".join(chunks)
        if self._yaml_structures:
            checked = PrivacyContent(
                result,
                self._uri,
                preserve_skill_metadata=self._preserve_skill_metadata,
                _structure_only=True,
            )
            if len(self._yaml_structures) != len(checked._yaml_nodes):
                raise self._structure_error()
            for index, (before, nodes) in enumerate(
                zip(self._yaml_structures, checked._yaml_nodes, strict=True)
            ):
                if index in split_documents:
                    valid = self._same_yaml_structure(self._yaml_key_aliases[index].nodes, nodes)
                else:
                    valid = before == [self._yaml_shape(node) for node in nodes]
                if not valid:
                    raise self._structure_error()
        return result

    def _structure_error(self) -> InvalidArgumentError:
        return InvalidArgumentError(
            "Privacy document structure validation failed",
            details={"uri": self._uri, "reason": "privacy_structure_changed"},
        )
