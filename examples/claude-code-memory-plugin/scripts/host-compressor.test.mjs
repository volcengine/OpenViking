import assert from "node:assert/strict";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createHostCompressor } from "./lib/host-compressor.mjs";

test("host compressor does not persist a Claude Code session per recall", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-host-compressor-"));
  const prevHome = process.env.HOME;
  // probeHostCli caches under ~/.openviking/state; keep it out of the real home.
  process.env.HOME = dir;
  try {
    const argsPath = join(dir, "args.json");
    const fakeCli = join(dir, "fake-claude");
    await writeFile(fakeCli, `#!/usr/bin/env node
require("node:fs").writeFileSync(${JSON.stringify(argsPath)}, JSON.stringify(process.argv.slice(2)));
process.stdout.write("- digest source: viking://user/x");
`);
    await chmod(fakeCli, 0o755);

    const run = await createHostCompressor({ recallRewrite: "client", recallCompressCommand: fakeCli });
    assert.equal(typeof run, "function");
    assert.equal(await run("prompt"), "- digest source: viking://user/x");

    const args = JSON.parse(await readFile(argsPath, "utf8"));
    assert.ok(args.includes("-p"));
    assert.ok(args.includes("--no-session-persistence"), `missing --no-session-persistence in ${args.join(" ")}`);
  } finally {
    process.env.HOME = prevHome;
    await rm(dir, { recursive: true, force: true });
  }
});
