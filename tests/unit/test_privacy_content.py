# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest
import yaml

from openviking.privacy.content import PrivacyContent
from openviking_cli.exceptions import InvalidArgumentError

REFERENCE = "{{ov_privacy:alice:viking://~/privacy/credential/demo#/values/key}}"


@pytest.mark.parametrize(
    "suffix,source,parse",
    [
        (
            "json",
            '{"private-value": "\\u0070rivate-value", "items": [42, true, null, 1208925819614629174706176]}',
            json.loads,
        ),
        (
            "jsonl",
            "\r\n".join(
                json.dumps({"text": char + "private-value" + char}, ensure_ascii=False)
                for char in ("\u0085", "\u2028", "\u2029")
            )
            + "\r\n",
            lambda value: [json.loads(line) for line in value.split("\n") if line.strip()],
        ),
        (
            "yaml",
            "empty: !!str\nshared: &empty !!str\ncopy: *empty\n"
            "key: &secret !!str >2-\n  private-value\nsecret: *secret\n"
            "literal: |\n  private-value\nfolded: >-\n  private-value\n  continued\n",
            yaml.safe_load,
        ),
        (
            "yaml",
            "%YAML 1.1\n---\nprivate-value: &shared private-value # keep comment\ncopy: *shared\n"
            'items: [yes, on, 03, 1.5, 2026-10-03, "yes", "03", null]\n'
            "...\n---\n...\n---\nkey: &shared private-value\nitems: [42, true, null, *shared]\n",
            lambda value: list(yaml.safe_load_all(value)),
        ),
    ],
    ids=["json", "jsonl-unicode", "yaml-blocks", "yaml-stream"],
)
def test_structured_values_roundtrip_without_changing_keys_or_types(
    suffix, source, parse, monkeypatch
):
    rotated = 'pa"ss\\word\n下一行🙂\u0085\u2028\u2029'
    loaders = (
        [yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)]
        if suffix == "yaml"
        else [None]
    )

    def replace_values(value):
        if isinstance(value, str):
            return value.replace("private-value", rotated)
        if isinstance(value, list):
            return [replace_values(item) for item in value]
        if isinstance(value, dict):
            return {key: replace_values(item) for key, item in value.items()}
        return value

    for loader in loaders:
        if loader is not None:
            monkeypatch.setattr("openviking.privacy.content._YAML_LOADER", loader)
        uri = "viking://resources/config." + suffix
        document = PrivacyContent(source, uri)
        protected = document.render(lambda item: item.value.replace("private-value", REFERENCE))
        restored = PrivacyContent(protected, uri).render(
            lambda item: item.value.replace(REFERENCE, rotated)
        )

        assert parse(restored) == replace_values(parse(source))
        if "# keep comment" in source:
            assert "# keep comment" in restored and "*shared" in restored


def test_markdown_frontmatter_and_body_use_their_own_encoding():
    source = "---\nname: private-value\nitems: [private-value, 42]\n---\n\n正文 private-value\n"
    protected = PrivacyContent(source, "viking://resources/guide.md").render(
        lambda item: item.value.replace("private-value", REFERENCE)
    )
    header, body = protected.split("---\n")[1:]
    assert yaml.safe_load(header) == {"name": REFERENCE, "items": [REFERENCE, 42]}
    assert body == "\n正文 " + REFERENCE + "\n"


def test_yaml_alias_cannot_change_a_key_indirectly():
    source = "? &key private-value\n: body\ncopy: *key\n"
    with pytest.raises(InvalidArgumentError, match="document structure"):
        PrivacyContent(source, "viking://resources/config.yaml").render(
            lambda item: item.value.replace("private-value", REFERENCE)
        )
