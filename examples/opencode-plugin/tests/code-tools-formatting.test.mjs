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
        location: { path: "docsuite/domains/std.py" },
        focus_symbols: [
          {
            name: "DiagnosticDomain._resolve_docref",
            range: { start_line: 805, end_line: 840 },
          },
        ],
        reasons: ["diagnostic emitter line matches issue"],
        snippets: [
          {
            line: 822,
            text: 'logger.warning(__("resource id is missing for %s: %s"), figtype, labelid)',
          },
          {
            line: 826,
            text: 'same-file diagnostic precedent: msg = __("Failed to resolve a document reference. A title or label was not found: %s")',
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
            text: 'assert "WARNING: resource id is missing for section: index" in warnings',
          },
          {
            line: 121,
            text: 'assert "WARNING: resource id is missing for section: index" in warnings',
          },
        ],
      },
    ],
    verification: [
      { command: "python3 -m py_compile docsuite/domains/std.py" },
      { command: "python3 -m pytest tests/test_build_html.py" },
    ],
  })

  assert.match(formatted, /^OpenViking staged action:\n/)
  assert.match(formatted, /- Classification: diagnostic wording or argument delta\./)
  assert.match(
    formatted,
    /- Completion criterion: patch the production diagnostic emitter and run the immediate static check\./,
  )
  assert.match(
    formatted,
    /- Keep the first pass limited to the listed edit target and behavior reference\./,
  )
  assert.match(
    formatted,
    /- Treat tests and assertions as behavior evidence unless the issue explicitly asks to update tests\./,
  )
  assert.match(formatted, /- Follow first: diagnostic wording delta/)
  assert.match(
    formatted,
    /- Edit line: docsuite\/domains\/std\.py:L822 logger\.warning/,
  )
  assert.match(
    formatted,
    /- Message shape line: docsuite\/domains\/std\.py:L826 same-file diagnostic precedent: msg = __\("Failed to resolve a document reference/,
  )
  assert.doesNotMatch(formatted, /- Behavior reference:/)
  assert.doesNotMatch(formatted, /- Reference line:/)
  assert.doesNotMatch(formatted, /- Related assertion line:/)
  assert.match(
    formatted,
    /- Minimal read window if needed: docsuite\/domains\/std\.py offset=822 limit=4/,
  )
  assert.match(
    formatted,
    /- Do not read a larger function or helper window before the first patch/,
  )
  assert.match(
    formatted,
    /- Patch draft: update the production diagnostic wording, arguments, or guard indicated by the edit line; use nearby same-file diagnostics only as style evidence\. Treat tests\/assertions as behavior evidence, not patch targets\./,
  )
  assert.doesNotMatch(formatted, /Replacement call sketch/)
  assert.doesNotMatch(formatted, /Expected runtime warning sketch/)
  assert.doesNotMatch(formatted, /Assertion rewrite sketch/)
  assert.doesNotMatch(formatted, /Behavior assertion lines/)
  assert.match(
    formatted,
    /- First patch contract: use the edit and message shape lines above to patch production code now\./,
  )
  assert.match(formatted, /Do not edit tests or assertions during this first patch unless the issue explicitly asks for test changes/)
  assert.match(
    formatted,
    /- If patch application fails, read the exact edit line and retry a minimal diagnostic patch before broadening/,
  )
  assert.doesNotMatch(formatted, /bad warning/)
  assert.match(
    formatted,
    /- If verification fails before test collection or during dependency imports, treat it as environment setup; do not broaden code search/,
  )
  assert.match(formatted, /- After reading the listed edit target, edit and verify before extra read\/grep\/glob\./)
  assert.match(
    formatted,
    /If the immediate static check passes, run only the narrow verification suggested by the result when available/,
  )
  assert.match(formatted, /- Verify immediate path: python3 -m py_compile docsuite\/domains\/std\.py/)
  assert.doesNotMatch(
    formatted,
    /Optional narrow test/,
  )
  assert.match(formatted, /Delay broad grep\/read\/codesearch until this patch and immediate verification path fails/)
  assert.doesNotMatch(formatted, /- Verify narrow path:/)
  assert.match(formatted, /Do not expand symbol ranges or inspect adjacent implementation until the listed diagnostic path fails/)
  assert.doesNotMatch(formatted, /Top edit candidates:/)
  assert.doesNotMatch(formatted, /focus:/)
  assert.doesNotMatch(formatted, /DiagnosticDomain\._resolve_docref L805-840/)
})

test("code locate output keeps normal guidance compact and top-first", () => {
  const candidates = Array.from({ length: 5 }, (_, index) => ({
    rank: index + 1,
    location: { path: `pkg/module_${index + 1}.py` },
    reasons: [`reason ${index + 1}`],
    snippets: [{ line: 10 + index, text: `def target_${index + 1}(): pass` }],
    next_action: "read this top edit file first",
  }))
  const references = Array.from({ length: 3 }, (_, index) => ({
    rank: index + 1,
    location: { path: `tests/test_module_${index + 1}.py` },
    reasons: [`reference ${index + 1}`],
    snippets: [{ line: 20 + index, text: `def test_target_${index + 1}(): pass` }],
  }))

  const formatted = formatCodeLocateOutput({
    schema_version: "code-locate/v1",
    summary_text: "Top edit candidate: pkg/module_1.py.",
    edit_candidates: candidates,
    behavior_references: references,
    verification: [
      { kind: "static", command: "python3 -m py_compile pkg/module_1.py" },
      { kind: "narrow_tests", command: "python3 -m pytest tests/test_module_1.py" },
      { kind: "narrow_tests", command: "python3 -m pytest tests/test_module_2.py" },
    ],
  })

  assert.match(formatted, /Contract: read the top edit candidate first/)
  assert.match(formatted, /Patch before broader grep\/read\/codesearch/)
  assert.match(formatted, /If pytest fails before collection or dependency imports/)
  assert.match(formatted, /pkg\/module_3\.py/)
  assert.doesNotMatch(formatted, /pkg\/module_4\.py/)
  assert.match(formatted, /tests\/test_module_2\.py/)
  assert.doesNotMatch(formatted, /tests\/test_module_3\.py/)
  assert.match(formatted, /python3 -m py_compile pkg\/module_1\.py/)
  assert.match(formatted, /python3 -m pytest tests\/test_module_1\.py/)
  assert.doesNotMatch(formatted, /python3 -m pytest tests\/test_module_2\.py/)
})
