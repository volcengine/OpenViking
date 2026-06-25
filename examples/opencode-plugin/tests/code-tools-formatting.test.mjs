import test from "node:test"
import assert from "node:assert/strict"
import { formatCodeLocateOutput, formatCodeSearchOutput } from "../lib/code-tools.mjs"

test("code search output adds local edit paths and limits result blocks", () => {
  const raw = [
    '7 code matches for "fixme" (scanned 200 files)',
    "",
    "viking://resources/repo/tests/checkers/unittest_misc.py",
    "  symbols: TestFixme L1-20",
    "  content:",
    "    L1: class TestFixme:",
    "    L2: def test_fixme(self):",
    "    L3: # FIXME",
    "    L4: MessageTest(msg_id=\"fixme\")",
    "",
    "viking://resources/repo/pylint/checkers/misc.py",
    "  symbols: EncodingChecker, EncodingChecker.open",
    "  content:",
    "    L80: warning notes in the code like FIXME",
    "    L98: \"notes\",",
    "    L128: self._fixme_pattern = re.compile(regex_string, re.I)",
    "",
    "viking://resources/repo/pylint/reporters/text.py",
    "  content:",
    "    L10: fixme",
  ].join("\n")

  const formatted = formatCodeSearchOutput(raw, {
    uri: "viking://resources/repo",
    projectDirectory: "/workspace/repo",
    maxFiles: 2,
    maxContentLines: 2,
  })

  assert.match(formatted, /Showing top 2 of 7 code matches/)
  assert.match(formatted, /local: \.\/tests\/checkers\/unittest_misc\.py/)
  assert.match(formatted, /local: \.\/pylint\/checkers\/misc\.py/)
  assert.doesNotMatch(formatted, /pylint\/reporters\/text\.py/)
  assert.match(formatted, /L80: warning notes/)
  assert.match(formatted, /L98: "notes"/)
  assert.doesNotMatch(formatted, /L128: self\._fixme_pattern/)
})

test("code locate output lifts patch-first staged action above ranked candidates", () => {
  const formatted = formatCodeLocateOutput({
    schema_version: "code-locate/v1",
    summary_text: "diagnostic wording delta; patch diagnostic message/arguments first",
    edit_candidates: [
      {
        rank: 1,
        location: { path: "sphinx/domains/std.py" },
        focus_symbols: [
          {
            name: "StandardDomain._resolve_numref_xref",
            range: { start_line: 805, end_line: 840 },
          },
        ],
        reasons: ["diagnostic emitter line matches issue"],
        snippets: [
          {
            line: 822,
            text: 'logger.warning(__("no number is assigned for %s: %s"), figtype, labelid)',
          },
          {
            line: 826,
            text: 'same-file diagnostic precedent: msg = __("Failed to create a cross reference. A title or caption not found: %s")',
          },
        ],
        next_action:
          "PATCH FIRST: diagnostic wording delta; patch only diagnostic message/arguments and matching assertions",
      },
    ],
    behavior_references: [
      {
        rank: 1,
        location: { path: "tests/test_build_html.py" },
        reasons: ["positive diagnostic assertion"],
        snippets: [
          {
            line: 120,
            text: 'assert "WARNING: no number is assigned for section: index" in warnings',
          },
          {
            line: 121,
            text: 'assert "WARNING: no number is assigned for section: index" in warnings',
          },
        ],
      },
    ],
    verification: [
      { command: "python3 -m py_compile sphinx/domains/std.py" },
      { command: "python3 -m pytest tests/test_build_html.py" },
    ],
  })

  assert.match(formatted, /^OpenViking staged action:\n/)
  assert.match(formatted, /- Classification: diagnostic wording delta, not a numbering\/builder regression\./)
  assert.match(
    formatted,
    /- Completion criterion: patch the production diagnostic emitter and run the immediate static check\./,
  )
  assert.match(
    formatted,
    /- If that static check passes, final-answer immediately; do not run grep\/read\/glob\/tests\/codesearch for extra confidence\./,
  )
  assert.match(
    formatted,
    /- Forbidden first-pass edits: tests, assertions, fixtures, builders, and numbering logic\./,
  )
  assert.match(formatted, /- Follow first: diagnostic wording delta/)
  assert.match(
    formatted,
    /- Edit line: sphinx\/domains\/std\.py:L822 logger\.warning/,
  )
  assert.match(
    formatted,
    /- Message shape line: sphinx\/domains\/std\.py:L826 same-file diagnostic precedent: msg = __\("Failed to create a cross reference/,
  )
  assert.doesNotMatch(formatted, /- Behavior reference:/)
  assert.doesNotMatch(formatted, /- Reference line:/)
  assert.doesNotMatch(formatted, /- Related assertion line:/)
  assert.match(
    formatted,
    /- Minimal read window if needed: sphinx\/domains\/std\.py offset=822 limit=4/,
  )
  assert.match(
    formatted,
    /- Do not read a larger function or helper window before the first patch/,
  )
  assert.match(
    formatted,
    /- Patch draft: replace the edit-line diagnostic wording in production code; borrow the cross-reference prefix\/style if useful, but keep the current emitter's reason semantics; pass the unresolved target\/label as the placeholder argument\. Treat tests\/assertions as read-only behavior evidence, not patch targets\./,
  )
  assert.match(
    formatted,
    /- Replacement call sketch: logger\.warning\(__\("Failed to create a cross reference\. Any number is not assigned: %s"\), labelid, location=node\)/,
  )
  assert.match(
    formatted,
    /- Expected runtime warning sketch: production warning should change from "WARNING: no number is assigned for section: index" to "WARNING: Failed to create a cross reference\. Any number is not assigned: index"/,
  )
  assert.doesNotMatch(formatted, /Assertion replacement sketch/)
  assert.doesNotMatch(formatted, /Behavior assertion lines/)
  assert.match(
    formatted,
    /- First patch contract: use the edit and message shape lines above to patch production code now\./,
  )
  assert.match(formatted, /Do not edit tests, assertions, fixtures, builders, or numbering logic during this first patch/)
  assert.match(
    formatted,
    /- If patch application fails, read the exact edit line and retry the same diagnostic patch; do not reinterpret that as a numbering\/toc\/builder failure/,
  )
  assert.match(
    formatted,
    /- Do not decide between a bad warning and a missing guard before this first patch/,
  )
  assert.match(
    formatted,
    /- If verification fails before test collection or during dependency imports, treat it as environment setup; do not broaden code search/,
  )
  assert.match(formatted, /- After reading the listed edit target, edit and verify before extra read\/grep\/glob\./)
  assert.match(
    formatted,
    /If the immediate static check passes after this diagnostic patch, stop; do not inspect visible tests or implementation logic for extra confidence/,
  )
  assert.match(formatted, /- Verify immediate path: python3 -m py_compile sphinx\/domains\/std\.py/)
  assert.doesNotMatch(
    formatted,
    /Optional narrow test/,
  )
  assert.match(formatted, /Delay broad grep\/read\/codesearch until this patch and immediate static check path fails/)
  assert.doesNotMatch(formatted, /- Verify narrow path:/)
  assert.match(formatted, /Do not expand symbol ranges or inspect adjacent implementation until the same diagnostic patch applies and its immediate static check fails/)
  assert.doesNotMatch(formatted, /Top edit candidates:/)
  assert.doesNotMatch(formatted, /focus:/)
  assert.doesNotMatch(formatted, /StandardDomain\._resolve_numref_xref L805-840/)
})
