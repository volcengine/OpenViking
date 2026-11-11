# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest
import yaml

from openviking.privacy.content import PrivacyContent

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
        (
            "yaml",
            "display: &label private-value\ncopy: *label\nlookup: {*label: 23}\n",
            yaml.safe_load,
        ),
        (
            "yaml",
            "? &label private-value\n: 23\ndisplay: *label\ncopy: *label\n",
            yaml.safe_load,
        ),
    ],
    ids=[
        "json",
        "jsonl-unicode",
        "yaml-blocks",
        "yaml-stream",
        "value-key-alias",
        "key-value-alias",
    ],
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


@pytest.mark.parametrize(
    "source",
    [
        "display: &key [private-value]\nlookup: {? *key: 23}\n",
        "display: &key {name: private-value}\nlookup: {? *key: 23}\n",
        "display: &key [*key, private-value]\nlookup: {? *key: 23}\n",
        "display: &key {self: *key, name: private-value}\nlookup: {? *key: 23}\n",
    ],
)
def test_yaml_alias_cannot_change_a_key_indirectly(source, monkeypatch):
    for loader in (yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)):
        monkeypatch.setattr("openviking.privacy.content._YAML_LOADER", loader)
        protected = PrivacyContent(source, "viking://resources/config.yaml").render(
            lambda item: item.value.replace("private-value", REFERENCE)
        )
        restored = PrivacyContent(protected, "viking://resources/config.yaml").render(
            lambda item: item.value.replace(REFERENCE, 'rotated"value\\path\n下一行')
        )
        for text, expected in [(protected, REFERENCE), (restored, 'rotated"value\\path\n下一行')]:
            node = yaml.compose(text, Loader=loader)
            value = node.value[0][1]
            key = node.value[1][1].value[0][0]
            if isinstance(value, yaml.SequenceNode):
                assert value.value[-1].value == expected
                assert key.value[-1].value == "private-value"
            else:
                assert value.value[-1][1].value == expected
                assert key.value[-1][1].value == "private-value"


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "# heading\ndisplay: &label private-value # value\n"
            "lookup: {*label: 23} # key\ncopy: *label\n",
            '# heading\ndisplay: &label "MASKED" # value\n'
            'lookup: {? &label_key "private-value": 23} # key\ncopy: *label\n',
        ),
        (
            "? &label private-value # key\n: 23\ndisplay: *label # value\ncopy: *label\n",
            "? &label private-value # key\n: 23\n"
            'display: &label_value "MASKED" # value\ncopy: *label_value\n',
        ),
        (
            "display: &label ordinary-label # value\nlookup: {*label: 23} # key\n"
            "secret: private-value\n",
            "display: &label ordinary-label # value\nlookup: {*label: 23} # key\n"
            'secret: "MASKED"\n',
        ),
    ],
)
def test_yaml_rewrites_only_changed_values_and_conflicting_aliases(source, expected):
    document = PrivacyContent(source, "viking://resources/config.yaml")
    assert document.render(lambda item: item.value.replace("private-value", "MASKED")) == expected


@pytest.mark.parametrize("flow", [False, True])
@pytest.mark.parametrize("long_value", [False, True])
def test_yaml_alias_keys_preserve_long_values_and_anchor_names(flow, long_value):
    secret = "private-value" * (100 if long_value else 1)
    anchor = "label" if long_value else "a" * 1020
    source = f'display: &{anchor} "{secret}"\n'
    for name in ("lookup", "copy"):
        source += f"{name}: {{*{anchor}: 23}}\n" if flow else f"{name}:\n  *{anchor}: 23\n"
    result = PrivacyContent(source, "viking://resources/config.yaml").render(
        lambda item: item.value.replace(secret, REFERENCE)
    )
    assert yaml.safe_load(result) == {
        "display": REFERENCE,
        "lookup": {secret: 23},
        "copy": {secret: 23},
    }
