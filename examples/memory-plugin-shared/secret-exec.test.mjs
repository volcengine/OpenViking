import test from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { PassThrough } from "node:stream";
import { EventEmitter } from "node:events";

import {
  SECRET_EXEC_USAGE,
  createMasker,
  credentialName,
  hasReference,
  posixShell,
  privacyResolver,
  rewriteCommand,
  runSecretExec,
  secretExecPreToolUse,
  shellQuote,
  wrapCommand,
} from "./lib/secret-exec.mjs";

const REF = "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/pat}}";
const REF2 = "{{ov_privacy:alice:viking://~/privacy/credential/open%20ai#/values/api_key}}";
const WRAPPER = { node: "/usr/bin/node", runner: "/plugins/openviking/1.0.0/scripts/secret-run.mjs" };
const ON = { secretExec: true, secretExecApproval: "ask" };

// Values the shell would treat as syntax if any of them reached the script text.
const HOSTILE = [
  "plain-token-123",
  "two  words\tand tab",
  "*",
  "$HOME ${PATH} $(echo PWNED) `echo PWNED`",
  "it's \"quoted\" \\ back\\slash",
  "line one\nline two\n",
  "'; echo PWNED; '",
  "\"; echo PWNED; \"",
  "EOF\nEOF",
];

const SHELLS = ["sh", "bash"].filter((shell) => spawnSync(shell, ["-c", "true"]).status === 0);

function run(shell, script, env = {}) {
  const result = spawnSync(shell, ["-c", script], { env: { PATH: process.env.PATH, ...env }, encoding: "utf8" });
  return { stdout: result.stdout, code: result.status };
}

test("only a canonical credential reference counts, with blanks allowed inside the braces", () => {
  assert.ok(hasReference(`x ${REF} y`));
  assert.ok(hasReference("{{ \tov_privacy:alice:viking://~/privacy/credential/github#/values/pat \t}}"));
  for (const other of [
    "{{ov_privacy:alice:viking://~/privacy/skill/github#/values/pat}}",
    "{{ov_privacy:skill:github:pat}}",
    "{{ov_privacy:session:abc:12345678}}",
    "{{ov_privacy:alice:viking://~/privacy/credential/github}}",
    "{{ov_privacy:alice:viking://~/privacy/credential/git'hub#/values/pat}}",
    "{{ov_privacy:ambiguous}}",
  ]) {
    assert.equal(hasReference(other), false, other);
  }
  assert.equal(credentialName(REF), "github/pat");
  assert.equal(credentialName(REF2), "open ai/api_key");
});

test("each quoting context gets the expansion from the RFC table", () => {
  const rows = [
    [`echo ${REF}`, 'echo "${OV_SECRET_0}"'],
    [`echo "Bearer ${REF}"`, 'echo "Bearer ${OV_SECRET_0}"'],
    [`echo 'Bearer ${REF}'`, `echo 'Bearer '"\${OV_SECRET_0}"''`],
    [`echo $'Bearer ${REF}\\n'`, `echo $'Bearer '"\${OV_SECRET_0}"$'\\n'`],
    [`cat <<EOF\nBearer ${REF}\nEOF`, "cat <<EOF\nBearer ${OV_SECRET_0}\nEOF"],
  ];
  for (const [command, script] of rows) {
    assert.deepEqual(rewriteCommand(command), { script, references: [REF] }, command);
  }
  for (const operator of ["<<'EOF'", '<<"EOF"', "<<\\EOF", '<<E"O"F', "<<- 'EOF'"]) {
    const { error } = rewriteCommand(`cat ${operator}\n${REF}\nEOF`);
    assert.match(error ?? "", /unquoted \(<<EOF\)/, operator);
    assert.match(error, /file-editing tool/, operator);
  }
});

test("the scanner follows escapes, comments, nesting and here-document variants", () => {
  const cases = [
    // An escaped quote opens nothing.
    [`echo \\"${REF}`, 'echo \\""${OV_SECRET_0}"'],
    [`echo "a\\"${REF}"`, 'echo "a\\"${OV_SECRET_0}"'],
    [`echo \\'${REF}`, `echo \\'"\${OV_SECRET_0}"`],
    [`echo $'it\\'s ${REF}'`, `echo $'it\\'s '"\${OV_SECRET_0}"$''`],
    // Command substitution inside double quotes is unquoted again.
    [`echo "$(printf %s ${REF})"`, 'echo "$(printf %s "${OV_SECRET_0}")"'],
    [`echo "$(printf '%s' '${REF}') ${REF}"`, `echo "$(printf '%s' ''"\${OV_SECRET_0}"'') \${OV_SECRET_0}"`],
    [`echo "$( (echo "(${REF})") )"`, 'echo "$( (echo "(${OV_SECRET_0})") )"'],
    [`echo "\`printf %s ${REF}\`"`, 'echo "`printf %s "${OV_SECRET_0}"`"'],
    [`echo $(( 1 + 2 )) ${REF}`, 'echo $(( 1 + 2 )) "${OV_SECRET_0}"'],
    // A comment hides what it holds, and an apostrophe in it opens nothing.
    [`true # it's ${REF}\necho ${REF}`, `true # it's ${REF}\necho "\${OV_SECRET_0}"`],
    [`echo a#${REF}`, 'echo a#"${OV_SECRET_0}"'],
    // A here-string is a word, not a here-document.
    [`cat <<< ${REF}`, 'cat <<< "${OV_SECRET_0}"'],
    [`cat <<-EOF\n\t${REF}\n\tEOF\necho ${REF}`, 'cat <<-EOF\n\t${OV_SECRET_0}\n\tEOF\necho "${OV_SECRET_0}"'],
    [`cat <<A <<B; echo '${REF}'\na ${REF}\nA\nb ${REF}\nB`,
      `cat <<A <<B; echo ''"\${OV_SECRET_0}"''\na \${OV_SECRET_0}\nA\nb \${OV_SECRET_0}\nB`],
    [`x=$(cat <<EOF\n'${REF}'\nEOF\n)`, "x=$(cat <<EOF\n'${OV_SECRET_0}'\nEOF\n)"],
    [`cat <<'EOF'\nno reference here\nEOF\necho ${REF}`, `cat <<'EOF'\nno reference here\nEOF\necho "\${OV_SECRET_0}"`],
    // A substitution inside a here-document body is unquoted shell again.
    [`cat <<EOF\n"${REF}" $(echo ${REF}) \`echo ${REF}\`\nEOF`,
      'cat <<EOF\n"${OV_SECRET_0}" $(echo "${OV_SECRET_0}") `echo "${OV_SECRET_0}"`\nEOF'],
    // A shift is not a here-document, so the lines after it are not its body.
    [`n=$((1<<4))\nprintf %s ${REF}`, 'n=$((1<<4))\nprintf %s "${OV_SECRET_0}"'],
    [`(( n = 1 << 4 ))\nprintf %s ${REF}`, '(( n = 1 << 4 ))\nprintf %s "${OV_SECRET_0}"'],
    [`echo "$(( (1<<2) + 1 ))" ${REF}`, 'echo "$(( (1<<2) + 1 ))" "${OV_SECRET_0}"'],
  ];
  for (const [command, script] of cases) {
    assert.equal(rewriteCommand(command).script, script, command);
  }
});

test("one reference is one variable, however often and however it is spaced", () => {
  const spaced = REF.replace("{{", "{{ ").replace("}}", "\t}}");
  const plan = rewriteCommand(`a ${REF} "${REF2}" '${spaced}' ${REF2}`);
  assert.deepEqual(plan.references, [REF, REF2]);
  assert.equal(plan.script, `a "\${OV_SECRET_0}" "\${OV_SECRET_1}" ''"\${OV_SECRET_0}"'' "\${OV_SECRET_1}"`);
  assert.deepEqual(rewriteCommand("echo no references").references, []);
});

test("a value full of shell syntax arrives verbatim and runs nothing, in every context", { skip: !SHELLS.length }, () => {
  // Command substitution drops trailing newlines whatever it substitutes.
  const substituted = (value) => value.replace(/\n+$/, "");
  const contexts = [
    [`printf '[%s]' ${REF}`],
    [`printf '[%s]' "${REF}"`],
    [`printf '[%s]' '${REF}'`],
    [`printf '[%s]' "$(printf '%s' '${REF}')"`, substituted],
    [`printf '[%s]' "\`printf '%s' ${REF}\`"`, substituted],
    [`n=$((1<<4))\nprintf '[%s]' ${REF}`],
  ];
  for (const shell of SHELLS) {
    // dash has no $'...' quoting at all.
    const ansi = run(shell, "printf %s $'a'").stdout === "a" ? [[`printf '[%s]' $'${REF}'`]] : [];
    for (const value of HOSTILE) {
      for (const [command, expect = (v) => v] of [...contexts, ...ansi]) {
        const { script } = rewriteCommand(command);
        assert.ok(!script.includes(value), "the value never enters the script");
        const { stdout, code } = run(shell, script, { OV_SECRET_0: value });
        assert.equal(code, 0, `${shell}: ${command}`);
        assert.equal(stdout, `[${expect(value)}]`, `${shell}: ${JSON.stringify(value)} in ${command}`);
      }
      const doc = run(shell, rewriteCommand(`cat <<EOF\n[${REF}]\nEOF`).script, { OV_SECRET_0: value });
      assert.equal(doc.stdout, `[${value}]\n`, `${shell}: here-document`);
      const inner = run(shell, rewriteCommand(`cat <<EOF\n[$(printf '%s' ${REF})]\nEOF`).script, { OV_SECRET_0: value });
      assert.equal(inner.stdout, `[${substituted(value)}]\n`, `${shell}: substitution in a here-document`);
    }
  }
});

test("a single-quoted string stays quoted after a reference", { skip: !SHELLS.length }, () => {
  const { script } = rewriteCommand(`printf '%s|' 'a ${REF} b' "$HOME"`);
  for (const shell of SHELLS) {
    assert.equal(run(shell, script, { OV_SECRET_0: "v a l", HOME: "/h" }).stdout, "a v a l b|/h|", shell);
  }
});

test("shellQuote round-trips through the shell and the wrapper keeps the command readable", { skip: !SHELLS.length }, () => {
  const command = `curl -H 'Authorization: Bearer ${REF}' "https://x/?a=$b"`;
  const wrapped = wrapCommand(command, WRAPPER);
  assert.equal(wrapped, `'/usr/bin/node' '/plugins/openviking/1.0.0/scripts/secret-run.mjs' 'curl -H '\\''Authorization: Bearer ${REF}'\\'' "https://x/?a=$b"'`);
  for (const shell of SHELLS) {
    assert.equal(run(shell, `printf '%s\\n' ${wrapped}`).stdout, `${WRAPPER.node}\n${WRAPPER.runner}\n${command}\n`);
    for (const value of HOSTILE) assert.equal(run(shell, `printf %s ${shellQuote(value)}`).stdout, value);
  }
});

test("a command that starts any install's runner is not wrapped again, and ask still confirms it", () => {
  const commands = [
    wrapCommand(`curl https://x/?k=${REF}`, WRAPPER),
    wrapCommand(`echo ${REF}`, { node: "/opt/node", runner: "/old/0.9.0/scripts/secret-run.mjs" }),
    // The reference is put together at run time, so only the runner shows.
    `P='{{ov_privacy:alice:viking:'; node /x/scripts/secret-run.mjs "printf %s \${P}//~/privacy/credential/github#/values/pat}}"`,
  ];
  for (const command of commands) {
    const event = { tool_name: "Bash", tool_input: { command } };
    assert.equal(secretExecPreToolUse(event, { secretExec: true }, WRAPPER), null, command);
    const out = secretExecPreToolUse(event, ON, WRAPPER).hookSpecificOutput;
    assert.equal(out.permissionDecision, "ask", command);
    assert.match(out.permissionDecisionReason, /starts the secret-exec runner itself/);
    assert.equal(out.updatedInput, undefined, command);
  }
  const other = wrapCommand(`echo ${REF}`, { node: "/n", runner: "/x/not-the-runner.mjs" });
  const out = secretExecPreToolUse({ tool_name: "Bash", tool_input: { command: other } }, ON, WRAPPER).hookSpecificOutput;
  assert.equal(out.updatedInput.command, wrapCommand(other, WRAPPER));
});

test("the hook does nothing while secret exec is off", () => {
  for (const event of [
    { tool_name: "Bash", tool_input: { command: `echo ${REF}` } },
    { tool_name: "mcp__plugin_openviking-memory_openviking__privacy_resolve", tool_input: { reference: REF } },
  ]) {
    assert.equal(secretExecPreToolUse(event, { secretExec: false }, WRAPPER), null);
    assert.equal(secretExecPreToolUse(event, {}, WRAPPER), null);
  }
});

test("a Bash command with a reference is wrapped, every other input field kept", () => {
  const input = {
    command: `curl -H "Authorization: Bearer ${REF}" -H "X-Key: ${REF2}" https://api.github.com/user`,
    description: "Call the API",
    timeout: 120000,
    run_in_background: false,
    dangerouslyDisableSandbox: true,
  };
  const out = secretExecPreToolUse({ tool_name: "Bash", tool_input: input }, ON, WRAPPER).hookSpecificOutput;
  assert.equal(out.hookEventName, "PreToolUse");
  assert.equal(out.permissionDecision, "ask");
  assert.deepEqual(out.updatedInput, { ...input, command: wrapCommand(input.command, WRAPPER) });
  assert.match(out.permissionDecisionReason, /github\/pat, open ai\/api_key/);
  assert.match(out.permissionDecisionReason, /at run time/);
  assert.match(out.permissionDecisionReason, /masked in the output/);
  assert.ok(!JSON.stringify(out).includes("OV_SECRET"), "the hook hands over the command, not the rewrite");
});

test("the user's own permission flow decides unless approval is ask, and nothing is ever allowed", () => {
  const event = { tool_name: "Bash", tool_input: { command: `echo ${REF}` } };
  for (const approval of [undefined, "inherit", "allow", "bogus"]) {
    const out = secretExecPreToolUse(event, { secretExec: true, secretExecApproval: approval }, WRAPPER).hookSpecificOutput;
    assert.ok(!("permissionDecision" in out), String(approval));
    assert.ok(out.updatedInput.command.startsWith("'/usr/bin/node' "));
  }
  const ask = secretExecPreToolUse(event, ON, WRAPPER).hookSpecificOutput;
  assert.equal(ask.permissionDecision, "ask");
});

test("the hook leaves alone what it has no business wrapping", () => {
  for (const event of [
    { tool_name: "Bash", tool_input: { command: "curl https://example.com" } },
    { tool_name: "Bash", tool_input: { command: `true # ${REF}` } },
    { tool_name: "Bash", tool_input: { command: [REF] } },
    { tool_name: "Bash" },
    { tool_name: "Monitor", tool_input: { command: `echo ${REF}` } },
    { tool_name: "Write", tool_input: { file_path: "/tmp/a", content: REF } },
    { tool_name: "mcp__plugin_openviking-memory_openviking__privacy_tree", tool_input: {} },
    {},
  ]) {
    assert.equal(secretExecPreToolUse(event, ON, WRAPPER), null, JSON.stringify(event));
  }
});

test("privacy_resolve and a quoted here-document are denied with a way forward", () => {
  for (const name of ["mcp__openviking__privacy_resolve", "mcp__plugin_openviking-memory_openviking__privacy_resolve"]) {
    const out = secretExecPreToolUse({ tool_name: name, tool_input: { reference: REF } }, ON, WRAPPER).hookSpecificOutput;
    assert.equal(out.permissionDecision, "deny", name);
    assert.match(out.permissionDecisionReason, /verbatim into the Bash command/);
    assert.equal(out.updatedInput, undefined);
  }
  const heredoc = secretExecPreToolUse(
    { tool_name: "Bash", tool_input: { command: `cat <<'EOF' > notes.md\n${REF}\nEOF` } }, ON, WRAPPER,
  ).hookSpecificOutput;
  assert.equal(heredoc.permissionDecision, "deny");
  assert.match(heredoc.permissionDecisionReason, /delimiter is quoted/);
});

test("the usage note tells the model where the reference goes and what not to do", () => {
  assert.match(SECRET_EXEC_USAGE, /verbatim in the Bash command/);
  assert.match(SECRET_EXEC_USAGE, /Do not call privacy_resolve/);
  assert.match(SECRET_EXEC_USAGE, /<<EOF/);
  assert.ok(!SECRET_EXEC_USAGE.includes("\n"), "one paragraph");
});

// ── masking ──────────────────────────────────────────────────────────────

function maskAll(secrets, chunks) {
  const masker = createMasker(secrets);
  return Buffer.concat([...chunks.map((chunk) => masker.write(Buffer.from(chunk))), masker.end()]).toString();
}

function everySplit(text) {
  const splits = [[text]];
  for (let at = 0; at <= text.length; at += 1) splits.push([text.slice(0, at), text.slice(at)]);
  splits.push([...text]);
  return splits;
}

test("a value split across chunks is masked at every offset", () => {
  const secrets = [{ value: "s3cr3t-T0KEN", reference: REF }];
  const text = "auth s3cr3t-T0KEN ok s3cr3t-T0KE s3cr3t-T0KENs3cr3t-T0KEN end s3cr";
  const expected = `auth ${REF} ok s3cr3t-T0KE ${REF}${REF} end s3cr`;
  for (const chunks of everySplit(text)) assert.equal(maskAll(secrets, chunks), expected, JSON.stringify(chunks));
});

test("overlapping values mask leftmost first, and longest first where they start together", () => {
  const secrets = [
    { value: "abcdef", reference: "<A>" },
    { value: "cdefgh", reference: "<B>" },
    { value: "abcdefXY", reference: "<LONG>" },
    { value: "cdef", reference: "<SHORT>" },
  ];
  const cases = [
    ["xabcdefghx", "x<A>ghx"],
    ["abcdefXY", "<LONG>"],
    ["abcdefZZ", "<A>ZZ"],
    ["zcdefghz", "z<B>z"],
    ["zcdefz", "z<SHORT>z"],
    ["abcdefX", "<A>X"],
  ];
  for (const [text, expected] of cases) {
    for (const chunks of everySplit(text)) assert.equal(maskAll(secrets, chunks), expected, JSON.stringify(chunks));
  }
});

test("output with no near-miss passes at once; only a possible start of a value waits", () => {
  const masker = createMasker([{ value: "s3cr3t-T0KEN", reference: REF }]);
  assert.equal(masker.write(Buffer.from("hello world\n")).toString(), "hello world\n");
  assert.equal(masker.write(Buffer.from("tail s3c")).toString(), "tail ");
  assert.equal(masker.write(Buffer.from("ond")).toString(), "s3cond");
  assert.equal(masker.write(Buffer.from("x s")).toString(), "x ");
  assert.equal(masker.end().toString(), "s");
  assert.equal(createMasker([]).write(Buffer.from("anything")).toString(), "anything");
});

test("a value saved under two fields masks to the smaller reference, whichever the command named first", () => {
  const zeta = REF.replace("/github#", "/zeta#");
  const alpha = REF.replace("/github#", "/alpha#");
  for (const order of [[zeta, alpha], [alpha, zeta]]) {
    const secrets = order.map((reference) => ({ value: "samevalue123", reference }));
    assert.equal(maskAll(secrets, ["a samevalue123 b"]), `a ${alpha} b`);
  }
});

test("a value is also masked as it reads inside a JSON string", () => {
  const value = 'pa"ss\\wörd\n';
  const masked = maskAll([{ value, reference: REF }], [JSON.stringify({ raw: value }), `\n${value}`]);
  assert.equal(masked, `{"raw":"${REF}"}\n${REF}`);
});

// ── the runner, with its effects injected ────────────────────────────────

function sinks() {
  const stdout = new PassThrough();
  const stderr = new PassThrough();
  const read = (stream) => {
    let text = "";
    stream.on("data", (chunk) => { text += chunk; });
    return () => text;
  };
  return { stdout, stderr, out: read(stdout), err: read(stderr) };
}

const SH = SHELLS[0] || "/bin/sh";

test("the runner refuses to start when any reference fails, and names only the reference", async () => {
  for (const answer of [
    () => ({ ok: false, reason: "NOT_FOUND" }),
    () => ({ ok: true, value: "" }),
    () => ({ ok: true, value: 42 }),
    () => { throw new Error("the value is s3cr3t-T0KEN"); },
    () => undefined,
  ]) {
    const io = sinks();
    let spawned = false;
    const code = await runSecretExec(`curl ${REF} ${REF2}`, {
      env: {},
      resolve: async (reference) => (reference === REF ? { ok: true, value: "s3cr3t-T0KEN" } : answer()),
      spawn: () => { spawned = true; },
      shell: SH,
      ...io,
      signals: new EventEmitter(),
    });
    assert.equal(code, 1);
    assert.equal(spawned, false);
    assert.equal(io.out(), "");
    assert.match(io.err(), new RegExp(`cannot resolve ${REF2.replace(/[{}$.*+?^()|[\]\\]/g, "\\$&")}`));
    assert.match(io.err(), /nothing was run/);
    assert.ok(!io.err().includes("s3cr3t"), "no value in any error");
    assert.ok(!io.err().includes(REF + " "), "the resolved reference is not reported");
  }
});

test("the runner refuses a quoted here-document, an empty command and nesting", async () => {
  for (const [command, env, message] of [
    [`cat <<'EOF'\n${REF}\nEOF`, {}, /delimiter is quoted/],
    ["", {}, /usage/],
    [`echo ${REF}`, { OV_SECRET_0: "outer" }, /inside another secret-exec/],
  ]) {
    const io = sinks();
    const code = await runSecretExec(command, {
      env,
      resolve: async () => ({ ok: true, value: "v-value" }),
      spawn: () => assert.fail("nothing may run"),
      shell: SH,
      ...io,
      signals: new EventEmitter(),
    });
    assert.equal(code, 1);
    assert.match(io.err(), message);
  }
});

test("the runner hands the values over through the environment and masks them on both streams", { skip: !SHELLS.length }, async () => {
  const io = sinks();
  const signals = new EventEmitter();
  const value = "v$(echo PWNED) 'q'";
  const code = await runSecretExec(`printf '%s|' ${REF} '${REF}'; printf '%s' "${REF}" >&2; exit 3`, {
    env: { PATH: process.env.PATH },
    resolve: async () => ({ ok: true, value }),
    spawn,
    shell: SH,
    ...io,
    signals,
  });
  assert.equal(code, 3);
  assert.equal(io.out(), `${REF}|${REF}|`);
  assert.equal(io.err(), REF);
  assert.equal(signals.listenerCount("SIGTERM"), 0, "the forwarders are removed");
});

test("a value the environment cannot carry stops the runner without naming the value", async () => {
  const io = sinks();
  const code = await runSecretExec(`echo ${REF}`, {
    env: { PATH: process.env.PATH },
    resolve: async () => ({ ok: true, value: "s3cr3t-T0KEN\u0000tail" }),
    spawn,
    shell: SH,
    ...io,
    signals: new EventEmitter(),
  });
  assert.equal(code, 1);
  assert.match(io.err(), /cannot start .* \(ERR_INVALID_ARG_VALUE\)/);
  assert.ok(!io.err().includes("s3cr3t"), io.err());
});

test("privacyResolver posts the reference as a selector and accepts only a stored text value", async () => {
  const calls = [];
  const fetchWith = (res) => async (path, init) => {
    calls.push({ path, init });
    return res;
  };
  assert.deepEqual(await privacyResolver(fetchWith({ ok: true, result: { value: "tok-123456" } }))(REF), { ok: true, value: "tok-123456" });
  assert.deepEqual(calls[0], {
    path: "/api/v1/privacy-configs/resolve",
    init: { method: "POST", body: JSON.stringify({ selector: REF }) },
  });
  for (const [res, reason] of [
    [{ ok: false, status: 404, error: { code: "NOT_FOUND", message: "x" } }, "NOT_FOUND"],
    [{ ok: false, status: 403, error: {} }, "HTTP 403"],
    [{ ok: false, status: 0, error: { message: "ECONNREFUSED" } }, "OpenViking is unreachable"],
    [{ ok: true, result: { value: "" } }, "no value is stored"],
    [{ ok: true, result: {} }, "no value is stored"],
    [{ ok: true, result: { value: { nested: true } } }, "the stored value is not text"],
    [{ ok: true, result: { value: 123456 } }, "the stored value is not text"],
    [{ ok: true, result: { value: "tok\u0000en" } }, "the stored value is not text"],
  ]) {
    assert.deepEqual(await privacyResolver(fetchWith(res))(REF), { ok: false, reason });
  }
});

test("the runner's shell is the one Claude Code's Bash tool picks", () => {
  const installed = (...paths) => (path) => paths.includes(path);
  const all = installed("/bin/zsh", "/usr/local/bin/bash", "/opt/bin/bash", "/usr/bin/zsh", "/bin/sh");
  assert.equal(posixShell({ SHELL: "/bin/zsh" }, all), "/bin/zsh");
  assert.equal(posixShell({ SHELL: "/usr/local/bin/bash" }, all), "/usr/local/bin/bash");
  assert.equal(posixShell({ CLAUDE_CODE_SHELL: "/usr/local/bin/bash", SHELL: "/bin/zsh" }, all), "/usr/local/bin/bash");
  // sh is dash on Debian, where `<<<` and `$'…'` fail although Claude Code would run them in bash.
  assert.equal(posixShell({ SHELL: "/bin/sh", PATH: "/usr/bin:/opt/bin" }, all), "/usr/bin/zsh");
  assert.equal(posixShell({ SHELL: "/usr/bin/fish", PATH: "/usr/bin:/opt/bin" }, installed("/opt/bin/bash")), "/opt/bin/bash");
  assert.equal(posixShell({ SHELL: "/usr/bin/bash", PATH: "/opt/bin" }, installed("/opt/bin/bash")), "/opt/bin/bash");
  assert.equal(posixShell({ SHELL: "/usr/bin/fish", PATH: "/usr/bin" }, installed()), "/bin/sh");
  assert.equal(posixShell({}), "/bin/sh");
});
