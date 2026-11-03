# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Immutable prompt blocks selected by the extraction output protocols.

Keep model-facing wording here and capability decisions in the renderers. Tuple
blocks contain individual contract lines; string blocks retain section spacing.
"""

READ_FORMAT_RULES = """
## Read Format Rules
- The read tool accepts `uri`, optional `offset` (0-indexed), and optional `limit`.
- Read content is returned in Claude Code format: each visible line is prefixed with `line_number<TAB>`.
"""
PATCH_CONTEXT_RULES = """
## Patch Context Rules
- When copying numbered memory context into SEARCH/REPLACE or DELETE operations, copy only the exact text after `line_number<TAB>`. Never include the line-number prefix itself in `search`, `replace`, or `delete`.
"""

PYTHON_FORMAT_RULES = (
    "## Output Format: restricted Python memory SDK",
    "Return only Python code, optionally wrapped in one ```python code fence.",
    "The code is interpreted as a restricted DSL and cannot access Python modules, files, or the network.",
)
PYTHON_CONTENT_RULE = (
    "Never return memory content as bare Markdown; all changes must be arguments to SDK calls."
)
PYTHON_STRING_LITERAL_RULE = (
    'ALWAYS use a triple-quoted string ("""...""") for EVERY natural-language argument '
    "(e.g. content, summary, goal), even one-liners. "
    "Prose frequently contains apostrophes (e.g. Evan's), quotes, colons, or dates that break "
    'single- or double-quoted literals. Inside triple quotes, escape any literal """ and '
    "backslash; never put a real newline inside a single- or double-quoted string."
)
PYTHON_COMMIT_RULE = (
    "You may end the program with sdk.commit(); when present it must be the final call. "
    "If there are no changes, return only sdk.commit()."
)
PYTHON_METHODS_HEADER = ("", "Available write methods for this extraction:")
PYTHON_ADD_ONLY_PREAMBLE = (
    *PYTHON_FORMAT_RULES,
    PYTHON_CONTENT_RULE,
    "Create new memories only, with complete field values; do not modify or delete existing memories.",
    PYTHON_STRING_LITERAL_RULE,
    "Only keyword arguments are accepted by create and set.",
    PYTHON_COMMIT_RULE,
    *PYTHON_METHODS_HEADER,
)
PYTHON_UPSERT_PREAMBLE = (
    *PYTHON_FORMAT_RULES,
    "The SDK is write-only. Existing-memory records are input context only. "
    "Use each object_name as an already-bound variable; do not output the "
    "records or redeclare the objects.",
    PYTHON_CONTENT_RULE,
    "Existing objects shown by the system may use obj.update(field=value, ...) to set complete "
    "scalar/immutable fields, or obj.delete(replacement=None).",
    "To change an existing string field, update it through the object's field attribute. Use the "
    "real field name (e.g. content), NOT the literal word 'field':",
    "  - obj.content.update(new_value): replace the whole field with a complete new string.",
    "A field attribute is a write handle only; you cannot read it as a string or call str methods on it.",
    "For string fields marked [replace], always pass the complete new value to "
    "obj.content.update(), including unchanged text, even for a small change.",
)
PYTHON_PATCH_RULES = (
    "  - obj.content.edit(search=..., replace=...): replace one exact snippet in an editable string (merge_op=patch).",
    "  - obj.content.drop(text=...): delete one exact snippet in an editable string (merge_op=patch).",
    "edit()/drop() may be chained, e.g. obj.content.edit(search='a', replace='b').drop(text='c'); do not mix them with .update() in one chain.",
    "Each search= (and drop text=) MUST be copied verbatim from the current field value in the object's existing-memory context record, and must occur exactly once. Decode JSON string escapes when copying the text. If the snippet appears more than once, include an adjacent unique line just before or after it so the match is unique. Never use text from the conversation or the new facts you intend to add as a search anchor; that text is not in the current content and the edit will fail.",
    "edit()/drop() only work on an existing memory's editable string field (merge_op=patch); new memories from create/set must be given complete field values.",
    "For existing editable strings (merge_op=patch), prefer the smallest unique edit()/drop(). Do not rewrite the entire field just to add or change a few facts; large full-content rewrites are more likely to be truncated or malformed. Use obj.content.update() only when most of such an editable string changes.",
    'Use triple-quoted strings ("""...""") for every search=/replace=/text= snippet too.',
)
PYTHON_UPSERT_FOOTER = (
    PYTHON_STRING_LITERAL_RULE,
    "Only keyword arguments are accepted by create, set, and obj.update(); "
    "a field's update() takes one positional string.",
    PYTHON_COMMIT_RULE,
    "Use each system-provided object_name exactly as the variable name shown. "
    "When a newly created memory is the replacement, assign its create call to "
    "a variable first, for example: canonical = sdk.create_<type>(...); "
    "duplicate_1.delete(replacement=canonical). Never recreate or rebind an "
    "existing object.",
    *PYTHON_METHODS_HEADER,
)
PYTHON_LINK_RULES = (
    "",
    "## Link Rules",
    "- Create links between memories when they have a meaningful and clear relationship "
    "(e.g. an event mentioning a person/entity, a preference about a place/activity). "
    "Do NOT force links between unrelated items.",
    "- Use obj.link(target, link_type='related_to', weight=0.5, match_text='<word>', "
    "description='') as a standalone "
    "statement after both objects exist.",
    "- match_text must be a single exact word that appears in the source object's content. "
    "It is where the link anchor is inserted. Choose the most specific identifying word "
    "(e.g. a person's name, a place, an activity).",
    "- link_type uses lowercase snake_case: related_to (default), belongs_to, "
    "derived_from, caused_by, contradicts, evolved_from.",
    "- For a newly created object, assign the create/set call to a variable first, then "
    "call .link() on it (see the preamble); existing objects use their provided variable names.",
    "- Links are optional; only create them when the relationship is explicit in the "
    "conversation. Do not link every memory to every other memory.",
    "",
    "Example — linking a newly created memory to another new one and to an existing binding "
    "(replace create_<type>/fields with methods and fields from the contract above):",
    "```python",
    "obj_a = sdk.create_<type_a>(...)  # a new memory, assigned to a variable",
    "obj_b = sdk.create_<type_b>(...)  # another new memory",
    'obj_a.link(obj_b, link_type="related_to", match_text="<word_in_obj_a>")',
    'obj_a.link(existing_1, link_type="related_to", match_text="<word_in_obj_a>")',
    "```",
)
PYTHON_LINKS_DISABLED = ("Links are disabled; obj.link(...) is unavailable.",)
PYTHON_SINGLETON_UPDATE_RULE = (
    " To change an already-shown existing object, update that object instead of calling set again."
)
PYTHON_ADD_ONLY_REFERENCE_RULES = """
## Memory Object Rules
- Create collection memories with listed sdk.create_<memory_type>(...) methods.
- Set a single-file memory with its listed sdk.set_<memory_type>(...) method; each target scope has only one such object.
"""
PYTHON_UPSERT_REFERENCE_RULES = """
## Memory Object Rules
- Update or delete an existing memory only through its system-provided bound object; never pass or construct a URI.
- Create collection memories with listed sdk.create_<memory_type>(...) methods.
- Set a single-file memory with its listed sdk.set_<memory_type>(...) method; each target scope has only one such object.
- Existing-object immutable fields are preserved by the system. URI identity fields may be
  updated when their schema allows it; changing one renames the memory object.
- If a rename target already exists, do not rename over it. Inspect both supplied objects, update the
  canonical target with every distinct fact, then delete the source with replacement=target.
- For canonical merges, use duplicate.delete(replacement=canonical); for pure deletes, call delete() without replacement.
- delete(replacement=canonical) discards the duplicate's content entirely and keeps only the canonical. Before deleting a duplicate, first fold every distinct valid fact it holds into the canonical using its schema-supported update methods; merging or compacting must never drop a unique fact that only the duplicate recorded.
"""
PYTHON_PATCH_REMOVAL_RULE = (
    "- delete() removes the whole object; use obj.content.drop(text=...) "
    "(with the real field name) for partial removal only on [editable string] "
    "fields. For [replace] fields, update the complete remaining value, "
    "preserving every unaffected fact.\n"
)
PYTHON_FULL_REMOVAL_RULE = (
    "- delete() removes the whole object. To remove only some content, update "
    "the complete affected field with its remaining value, preserving every "
    "unaffected fact.\n"
)

JSON_ADD_ONLY_REFERENCE_RULES = """
## Page ID Rules
- Every new memory item MUST include a unique "page_id" >= 100.
- Create new memories only, with complete field values; do not modify or delete existing memories.
"""
JSON_UPSERT_REFERENCE_RULES = """
## Page ID Rules
- Every memory item you create or edit MUST include "page_id".
- For existing items, use the page_id shown in the supplied memory context.
- For new items, assign a unique page_id >= 100.
- When editing an existing item, reuse its existing page_id.
- To delete an existing item, add an entry to `delete_ids` using its page_id.
- `delete_ids` deletes the whole item: use it only if every substantive fact is in scope; never infer scope from the file name/topic.
- For canonical merges, set `replacement_page_id` to the surviving page that should inherit the deleted page's existing links/backlinks; for pure deletes, set `replacement_page_id` to null.
"""
JSON_PATCH_REMOVAL_RULE = (
    "- For partial removal on patch fields, use DELETE blocks for affected "
    "lines, preserving the rest. For replace fields, rewrite the complete "
    "remaining field value, preserving every unaffected fact.\n"
)
JSON_FULL_REMOVAL_RULE = (
    "- For partial removal, rewrite the complete affected field with its "
    "remaining value, preserving every unaffected fact.\n"
)
JSON_LINK_RULES = """
## Link Rules
- Link fields `f` and `t` must reference these page_id values.
- Only create links when the relationship is meaningful and clear from the conversation. Do NOT force links between unrelated items.
"""
