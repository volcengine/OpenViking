import test from "node:test"
import assert from "node:assert/strict"
import {
  applyInputFilters,
  compileInputFilters,
  describeInputFilters,
  parseInputFilterRule,
} from "./lib/input-filters.mjs"

function compile(rules) {
  const compiled = compileInputFilters(rules)
  assert.deepEqual(compiled.errors, [], `unexpected errors: ${JSON.stringify(compiled.errors)}`)
  return compiled.rules
}

function apply(rules, text, options) {
  return applyInputFilters(text, compile(rules), options)
}

test("parses a substitute rule with flags", () => {
  const rule = parseInputFilterRule(String.raw`s/^\s*ultrathink\s+//i`)
  assert.equal(rule.error, undefined)
  assert.equal(rule.op, "s")
  assert.equal(rule.scope, "")
  assert.equal(rule.replacement, "")
  assert.equal(rule.re.source, String.raw`^\s*ultrathink\s+`)
  assert.equal(rule.re.flags, "i")
})

test("accepts any punctuation delimiter, including a colon", () => {
  for (const rule of ["s|a|b|", "s#a#b#", "s:a:b:", "s,a,b,", "s!a!b!"]) {
    const parsed = parseInputFilterRule(rule)
    assert.equal(parsed.error, undefined, `${rule} should parse`)
    assert.equal(parsed.re.source, "a")
    assert.equal(parsed.replacement, "b")
  }
})

test("rejects alphanumeric, whitespace and backslash delimiters", () => {
  for (const rule of ["sxaxbx", "s a b ", "s\\a\\b\\"]) {
    assert.match(parseInputFilterRule(rule).error, /invalid delimiter|missing/)
  }
})

test("only the delimiter is un-escaped; every other escape reaches RegExp", () => {
  const parsed = parseInputFilterRule(String.raw`s/a\/b\d\\c/x/`)
  assert.equal(parsed.re.test("a/b7\\c"), true)
  assert.equal(parsed.re.test(String.raw`a\/b7\\c`), false)
  assert.equal(apply([String.raw`s/a\/b\d\\c/x/`], "a/b7\\c!").text, "x!")
})

test("\\x2c is an env-safe literal comma, but not a quantifier bound", () => {
  assert.equal(apply([String.raw`s/hey\x2c\s*//i`], "Hey, what changed").text, "what changed")
  // `{2\x2c}` is not quantifier syntax: RegExp falls back to matching it
  // literally. Bounded quantifiers have to come from the ovcli.conf array.
  assert.equal(apply([String.raw`s/a{2\x2c}/X/`], "aaa").text, "aaa")
  assert.equal(apply([String.raw`s/a{2\x2c}/X/`], "a{2,}").text, "X")
})

test("drop and keep rules take optional trailing delimiter and flags", () => {
  for (const rule of ["d/foo", "d/foo/", "d/foo/i"]) {
    assert.equal(parseInputFilterRule(rule).error, undefined, `${rule} should parse`)
  }
  assert.equal(parseInputFilterRule("d/foo/i").re.flags, "i")
})

test("g is stripped from d/k rules so .test() stays stateless", () => {
  const rules = compile(["d/foo/g"])
  assert.equal(rules[0].re.flags, "")
  // With `g` kept, .test() would advance lastIndex and alternate between calls.
  assert.equal(applyInputFilters("foo", rules).dropped, true)
  assert.equal(applyInputFilters("foo", rules).dropped, true)
})

test("recognises the role scope prefixes", () => {
  assert.equal(parseInputFilterRule("user:d/foo/").scope, "user")
  assert.equal(parseInputFilterRule("assistant:s/foo/bar/").scope, "assistant")
  assert.equal(parseInputFilterRule("s/user:/x/").scope, "")
})

test("reports every malformed rule instead of throwing", () => {
  const cases = [
    ["", /rule is empty/],
    ["   ", /rule is empty/],
    ["strip the prefix", /invalid delimiter "t"/],
    ["x/a/b/", /unknown operation "x"/],
    ["s", /missing delimiter/],
    ["s/abc", /missing "\/" after the pattern/],
    ["s/secret/", /missing "\/" after the replacement/],
    ["s///", /pattern is empty/],
    ["d//", /pattern is empty/],
    ["d/foo/x", /unknown flag "x"/],
    ["d/foo/ii", /duplicate flag "i"/],
    ["s/(/x/", /invalid regular expression/],
    [String.raw`d/[\w-.]/u`, /invalid regular expression/],
  ]
  for (const [rule, expected] of cases) {
    assert.match(parseInputFilterRule(rule).error || "", expected, `for rule ${JSON.stringify(rule)}`)
  }
  assert.match(parseInputFilterRule(42).error, /rule must be a string/)
})

test("non-array and non-string entries never throw", () => {
  assert.deepEqual(compileInputFilters(undefined).rules, [])
  assert.deepEqual(compileInputFilters("s/a/b/").rules, [])
  const compiled = compileInputFilters([null, "", "  ", "s/a/b/"])
  assert.equal(compiled.rules.length, 1)
  assert.equal(compiled.errors.length, 1)
  assert.match(compiled.errors[0].message, /rule must be a string/)
})

test("substitutions run in order, once unless g is set", () => {
  assert.equal(apply(["s/a/b/"], "aaa").text, "baa")
  assert.equal(apply(["s/a/b/g"], "aaa").text, "bbb")
  assert.equal(apply(["s/a/b/g", "s/b/c/"], "aa").text, "cb")
})

test("replacement honours $1, $& and $$", () => {
  assert.equal(apply(["s/(\\w+) (\\w+)/$2 $1/"], "hello world").text, "world hello")
  assert.equal(apply(["s/world/[$&]/"], "hello world").text, "hello [world]")
  assert.equal(apply(["s/world/$$/"], "hello world").text, "hello $")
})

test("d drops on a match and k drops when there is no match", () => {
  const dropped = apply(["d/^\\//"], "/help")
  assert.equal(dropped.dropped, true)
  assert.equal(dropped.op, "d")
  assert.equal(dropped.ruleIndex, 0)
  assert.equal(dropped.text, "")

  assert.equal(apply(["d/^\\//"], "how do I").dropped, false)
  assert.equal(apply(["k/^\\?ov\\b/"], "?ov what did we decide").dropped, false)
  assert.equal(apply(["k/^\\?ov\\b/"], "what did we decide").dropped, true)
})

test("chained keep rules are an AND, and the reported index is the config index", () => {
  const rules = compile(["s/^x //", "k/alpha/", "k/beta/"])
  assert.equal(applyInputFilters("x alpha beta", rules).dropped, false)
  const verdict = applyInputFilters("x alpha only", rules)
  assert.equal(verdict.dropped, true)
  assert.equal(verdict.ruleIndex, 2)
  assert.equal(verdict.op, "k")
})

test("scoped rules only fire for their role", () => {
  const rules = compile(["user:d/secret/"])
  assert.equal(applyInputFilters("secret", rules, { role: "user" }).dropped, true)
  assert.equal(applyInputFilters("secret", rules, { role: "assistant" }).dropped, false)
  assert.equal(applyInputFilters("secret", rules, { role: "" }).dropped, false)
})

test("text emptied by a substitution is not a drop", () => {
  const verdict = apply(["s/^ultrathink$//"], "ultrathink")
  assert.equal(verdict.dropped, false)
  assert.equal(verdict.text, "")
  assert.equal(verdict.changed, true)
})

test("substituteOnly rewrites but never drops", () => {
  const rules = compile(["s/secret/[redacted]/g", "d/^drop me/"])
  const verdict = applyInputFilters("drop me: secret secret", rules, { substituteOnly: true })
  assert.equal(verdict.dropped, false)
  assert.equal(verdict.text, "drop me: [redacted] [redacted]")
})

test("describeInputFilters summarises both knobs for the doctors", () => {
  const described = describeInputFilters({
    recallQueryFilters: ["s/^hi //", "d/^\\//", "k/x/"],
    captureFilters: ["s/(/x/"],
  })
  assert.deepEqual(described.map((entry) => entry.key), ["recallQueryFilters", "captureFilters"])
  assert.equal(described[0].env, "OPENVIKING_RECALL_QUERY_FILTERS")
  assert.equal(described[0].summary, "3 rules (1 substitute, 1 drop, 1 keep-only)")
  assert.equal(described[0].errors.length, 0)
  assert.equal(described[1].total, 1)
  assert.equal(described[1].active, 0)
  assert.equal(described[1].errors.length, 1)
  assert.deepEqual(describeInputFilters({}).map((entry) => entry.summary), ["0 rules", "0 rules"])
})

// Every rule printed in the plugin READMEs, so a broken example cannot ship.
test("the documented examples parse and behave as documented", () => {
  const strip = String.raw`s/^\s*(ultrathink|think harder?)\s+//i`
  assert.equal(apply([strip], "Ultrathink what did we decide").text, "what did we decide")

  const skipCommands = String.raw`d|^\s*[/!]|`
  assert.equal(apply([skipCommands], "/help").dropped, true)
  assert.equal(apply([skipCommands], "!ls -la").dropped, true)
  assert.equal(apply([skipCommands], "how do I run it").dropped, false)

  const optIn = [String.raw`k/^\?ov\b/`, String.raw`s/^\?ov\s*//`]
  assert.equal(apply(optIn, "?ov the retry policy").text, "the retry policy")
  assert.equal(apply(optIn, "the retry policy").dropped, true)

  const redact = String.raw`s/\b(sk|ghp|xoxb)_[A-Za-z0-9_-]+/[redacted]/g`
  assert.equal(
    apply([redact], "use sk_live_ABCDEFGHIJ and ghp_0123456789abcdef now").text,
    "use [redacted] and [redacted] now",
  )

  const skipCommandTurns = String.raw`user:d/^\s*\/(clear|compact)\b/`
  assert.equal(apply([skipCommandTurns], "/compact please", { role: "user" }).dropped, true)
  assert.equal(apply([skipCommandTurns], "/compact please", { role: "assistant" }).dropped, false)

  const politeness = String.raw`s/^(请|麻烦)(你|帮我)?//`
  assert.equal(apply([politeness], "请帮我看看这个报错").text, "看看这个报错")
})
