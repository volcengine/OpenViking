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


@dataclass(frozen=True, slots=True)
class ContentString:
    start: int
    end: int
    value: str
    quoted: bool = False


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
            structure = [self._yaml_shape(node) for node in nodes]
        except RecursionError:
            raise self._structure_error() from None
        except yaml.YAMLError:
            self.strings.append(ContentString(offset, offset + len(content), content))
            return
        self._yaml_structures.append(structure)
        if self._structure_only:
            return
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
        seen: dict[tuple[int, bool], int] | None = None,
        *,
        key: bool = False,
    ) -> object:
        if node is None:
            return None
        if seen is None:
            seen = {}
        # An alias first visited as a value must still preserve its content as a key.
        identity = (id(node), key)
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

    def render(self, replace: Callable[[ContentString], str]) -> str:
        chunks: list[str] = []
        cursor = 0
        for item in self.strings:
            value = replace(item)
            if value == item.value:
                continue
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
            chunks.extend((self.content[cursor : item.start], value))
            cursor = item.end
        if not chunks:
            return self.content
        chunks.append(self.content[cursor:])
        result = "".join(chunks)
        if (
            self._yaml_structures
            and self._yaml_structures
            != PrivacyContent(
                result,
                self._uri,
                preserve_skill_metadata=self._preserve_skill_metadata,
                _structure_only=True,
            )._yaml_structures
        ):
            raise self._structure_error()
        return result

    def _structure_error(self) -> InvalidArgumentError:
        return InvalidArgumentError(
            "Privacy document structure validation failed",
            details={"uri": self._uri, "reason": "privacy_structure_changed"},
        )
