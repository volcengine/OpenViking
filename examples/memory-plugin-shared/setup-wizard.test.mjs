import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough, Writable } from "node:stream";
import test from "node:test";
import { runSetupWizard } from "./lib/setup-wizard.mjs";

/**
 * Drive the wizard with scripted answers: readline writes a prompt without a
 * trailing newline, so every such chunk gets the next answer fed back.
 */
function scriptedIo(answers) {
  const queue = [...answers];
  const input = new PassThrough();
  const written = [];
  const output = new Writable({
    write(chunk, _enc, cb) {
      const text = chunk.toString();
      written.push(text);
      if (!text.endsWith("\n")) {
        setImmediate(() => input.write(`${queue.shift() ?? ""}\n`));
      }
      cb();
    },
  });
  return { input, output, transcript: () => written.join("") };
}

test("a missing config file is created at the candidate path", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-wizard-new-"));
  const target = join(dir, "nested", "ovcli.conf");
  try {
    const io = scriptedIo(["1", "sk-test", "y"]);
    const result = await runSetupWizard({
      input: io.input,
      output: io.output,
      env: { OPENVIKING_CLI_CONFIG_FILE: target },
    });

    assert.equal(result.written, true);
    assert.equal(result.path, target);
    assert.match(io.transcript(), new RegExp(`Config file: ${target}`));
    assert.deepEqual(JSON.parse(await readFile(target, "utf-8")), {
      url: "http://127.0.0.1:1933",
      api_key: "sk-test",
    });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("an existing config file keeps unknown fields and its secret", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-wizard-existing-"));
  const target = join(dir, "ovcli.conf");
  try {
    await writeFile(
      target,
      JSON.stringify({ url: "https://old.example.com", api_key: "sk-old", account: "acct" }),
    );
    const io = scriptedIo(["1", "", "y"]);
    const result = await runSetupWizard({
      input: io.input,
      output: io.output,
      env: { OPENVIKING_CLI_CONFIG_FILE: target },
    });

    assert.equal(result.path, target);
    assert.deepEqual(JSON.parse(await readFile(target, "utf-8")), {
      url: "http://127.0.0.1:1933",
      api_key: "sk-old",
      account: "acct",
    });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("declining the confirmation writes nothing but still names the target", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-wizard-abort-"));
  const target = join(dir, "missing", "ovcli.conf");
  try {
    await mkdir(join(dir, "missing"), { recursive: true });
    const io = scriptedIo(["2", "sk-test", "n"]);
    const result = await runSetupWizard({
      input: io.input,
      output: io.output,
      env: { OPENVIKING_CLI_CONFIG_FILE: target },
    });

    assert.equal(result.written, false);
    assert.equal(result.path, target);
    await assert.rejects(() => readFile(target, "utf-8"));
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
