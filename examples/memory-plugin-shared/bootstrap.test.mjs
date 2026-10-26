import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const bootstrap = join(dirname(fileURLToPath(import.meta.url)), "bootstrap.sh");

// Prints what the bootstrap handed it, one bracketed argument per line so
// arguments with spaces stay distinguishable, and exits with $FAKE_EXIT.
const FAKE_INSTALLER = `#!/usr/bin/env bash
#
# OpenViking Memory Plugin shared installer (test double).
for arg in "$@"; do printf 'arg=[%s]\\n' "$arg"; done
echo "site=$OPENVIKING_INSTALL_SITE"
echo "base=$OPENVIKING_DOWNLOAD_BASE"
echo "reexec=$OPENVIKING_INSTALLER_REEXEC"
if [ -c /dev/stdin ]; then echo "stdin=device"; else echo "stdin=other"; fi
exit "\${FAKE_EXIT:-0}"
`;

async function serve(body) {
  const server = createServer((req, res) => {
    if (req.url !== "/memory-plugin-shared/install.sh") {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { "content-type": "text/plain" }).end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return server;
}

async function withFixture(body, fn) {
  const server = await serve(body);
  const root = mkdtempSync(join(tmpdir(), "openviking-bootstrap-"));
  const tmp = join(root, "tmp");
  mkdirSync(tmp);
  try {
    return await fn({ base: `http://127.0.0.1:${server.address().port}`, root, tmp });
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
}

// detached puts the child in a new session, so it has no controlling terminal
// and /dev/tty cannot be opened, whatever terminal the test runner has.
function run(args, { base, tmp, env = {}, stdin = "" }) {
  const { OPENVIKING_INSTALL_SITE, OPENVIKING_INSTALLER_REEXEC, ...inherited } = process.env;
  const childEnv = { ...inherited, OPENVIKING_DOWNLOAD_BASE: base, TMPDIR: tmp, ...env };
  return new Promise((resolve, reject) => {
    const child = spawn("bash", args, { env: childEnv, detached: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (status) => resolve({ status, stdout, stderr }));
    child.stdin.end(stdin);
  });
}

function argsOf(stdout) {
  return stdout.split("\n").filter((line) => line.startsWith("arg=")).map((line) => line.slice(5, -1));
}

test("passes the user's arguments through unchanged after --dist tos", async () => {
  await withFixture(FAKE_INSTALLER, async (fixture) => {
    const result = await run(
      [bootstrap, "--yes", "--url", "http://127.0.0.1:1933", "--api-key", "key with spaces", ""],
      fixture,
    );
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(argsOf(result.stdout), [
      "--dist", "tos", "--yes", "--url", "http://127.0.0.1:1933", "--api-key", "key with spaces", "",
    ]);
    assert.match(result.stdout, /^site=$/m, "the installer picks the version-check site");
    assert.match(result.stdout, /^reexec=0$/m);
    assert.match(result.stdout, /^stdin=device$/m, "without a terminal the installer reads /dev/null");
    assert.deepEqual(readdirSync(fixture.tmp), [], "the downloaded installer is removed");
  });
});

test("an explicit OPENVIKING_INSTALL_SITE is kept", async () => {
  await withFixture(FAKE_INSTALLER, async (fixture) => {
    const result = await run([bootstrap], { ...fixture, env: { OPENVIKING_INSTALL_SITE: "http://site.test" } });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /^site=http:\/\/site\.test$/m);
  });
});

test("the harness variable adds --harness before the user's arguments", async () => {
  const source = readFileSync(bootstrap, "utf8");
  assert.equal(source.split("\n").filter((line) => line === 'harness=""').length, 1);
  await withFixture(FAKE_INSTALLER, async (fixture) => {
    const variant = join(fixture.root, "install-claude.sh");
    writeFileSync(variant, source.replace('harness=""', 'harness="claude"'));
    const result = await run([variant, "--yes"], fixture);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(argsOf(result.stdout), ["--dist", "tos", "--harness", "claude", "--yes"]);
  });
});

test("refuses to run an HTML page served in place of the installer", async () => {
  const page = "<!doctype html>\n<html><body>OpenViking Memory Plugin shared installer</body></html>\n";
  await withFixture(page, async (fixture) => {
    const result = await run([bootstrap, "--yes"], fixture);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /install\.sh is not the OpenViking installer/);
    assert.deepEqual(readdirSync(fixture.tmp), []);
  });
});

test("fails when the download fails", async () => {
  await withFixture(FAKE_INSTALLER, async (fixture) => {
    const result = await run([bootstrap], { ...fixture, base: `${fixture.base}/missing` });
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /could not download/);
  });
});

test("exits with the installer's status", async () => {
  await withFixture(FAKE_INSTALLER, async (fixture) => {
    const result = await run([bootstrap], { ...fixture, env: { FAKE_EXIT: "3" } });
    assert.equal(result.status, 3);
    assert.deepEqual(readdirSync(fixture.tmp), []);
  });
});

test("works as `cat bootstrap.sh | bash -s -- ...` without a terminal", async () => {
  await withFixture(FAKE_INSTALLER, async (fixture) => {
    const result = await run(["-s", "--", "--yes", "--harness", "codex"], {
      ...fixture,
      stdin: readFileSync(bootstrap, "utf8"),
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(argsOf(result.stdout), ["--dist", "tos", "--yes", "--harness", "codex"]);
    assert.match(result.stdout, /^stdin=device$/m);
  });
});

test("runs the installer from a directory of its own, not from the shared temp dir", async () => {
  const installer = `#!/usr/bin/env bash
#
# OpenViking Memory Plugin shared installer (test double).
dir="$(cd "$(dirname "\${BASH_SOURCE[0]}")" && pwd -P)"
[ -e "$dir/lib/install" ] && echo "stray=seen" || echo "stray=unseen"
`;
  await withFixture(installer, async (fixture) => {
    mkdirSync(join(fixture.tmp, "lib", "install"), { recursive: true });
    const result = await run([bootstrap], fixture);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /^stray=unseen$/m);
    assert.deepEqual(readdirSync(fixture.tmp), ["lib"]);
  });
});

const PAGE = "<!doctype html>\n<html><body>OpenViking Memory Plugin shared installer</body></html>\n";

test("of several download locations, one that works serves the install", async () => {
  await withFixture(FAKE_INSTALLER, async (fixture) => {
    const page = await serve(PAGE);
    try {
      const others = ["http://127.0.0.1:1/dl/", `http://127.0.0.1:${page.address().port}`];
      for (const bases of [[...others, `${fixture.base}/`], [fixture.base, ...others]]) {
        const result = await run([bootstrap, "--yes"], { ...fixture, base: bases.join(" ") });
        assert.equal(result.status, 0, result.stderr);
        assert.deepEqual(argsOf(result.stdout), ["--dist", "tos", "--yes"]);
        assert.match(result.stdout, new RegExp(`^base=${fixture.base}$`, "m"), "the installer keeps downloading from it");
        assert.deepEqual(readdirSync(fixture.tmp), []);
      }
    } finally {
      page.close();
    }
  });
});

test("the download location can be given as OPENVIKING_TOS_BASE", async () => {
  await withFixture(FAKE_INSTALLER, async (fixture) => {
    const result = await run([bootstrap], {
      ...fixture,
      env: { OPENVIKING_DOWNLOAD_BASE: undefined, OPENVIKING_TOS_BASE: fixture.base },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, new RegExp(`^base=${fixture.base}$`, "m"));
  });
});
