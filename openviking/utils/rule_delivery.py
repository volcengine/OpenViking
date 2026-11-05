"""Bound inline Rule delivery without cutting constraints or exceptions."""

import re

MAX_RULE_RESULT_CHARS = 50_000
_START = re.compile(r"^## \[[^\n]+\] [^\n]*\n\nConstraint:\n", re.MULTILINE)
_END = re.compile(r"\nCompiled Rule URI: viking://[^\n]+(?:\nPolicy sources:\n(?:- [^\n]+\n?)+)?$")


def bound_rule_text(text: str, budget: int = MAX_RULE_RESULT_CHARS, notice: str = "") -> str:
    if len(text) <= budget:
        return text
    matched = re.search(r"^Matched (\d+) active Rule\(s\)\.$", text, re.MULTILINE)
    starts = list(_START.finditer(text))
    blocks = [
        text[
            start.start() : starts[index + 1].start() if index + 1 < len(starts) else len(text)
        ].rstrip()
        for index, start in enumerate(starts)
    ]
    valid = (
        text.startswith("# Applicable OpenViking Rules\n")
        and matched is not None
        and int(matched[1]) == len(blocks)
        and all(_END.search(block) for block in blocks)
    )
    header = text[: starts[0].start()].rstrip() if valid and starts else ""
    kept: list[str] = []

    def incomplete(count: int) -> str:
        omitted = str(len(blocks) - count) if valid else "unknown"
        result = (
            f"[OpenViking] delivery.complete=false; omitted_rules={omitted}. "
            "Do not act until remaining Rules are checked. Retrieve smaller results "
            "or read compiled Rule URIs."
        )
        if valid and count < len(blocks):
            uri = re.search(r"\nCompiled Rule URI: ([^\n]+)", blocks[count])
            if uri and len(uri[1]) <= 500:
                result += f" First omitted Rule: {uri[1]}"
        return result + (f" {notice}" if notice else "")

    if valid:
        for block in blocks:
            if len("\n\n".join([header, *kept, block, incomplete(len(kept) + 1)])) > budget:
                break
            kept.append(block)
    output = "\n\n".join(part for part in [header, *kept, incomplete(len(kept))] if part)
    if len(output) > budget:
        output = incomplete(0)
    if len(output) > budget:
        output = "[OpenViking] delivery.complete=false. Retrieve remaining Rules before acting."
    if len(output) > budget:
        raise ValueError("Rule delivery budget cannot fit an incomplete-delivery notice")
    return output
