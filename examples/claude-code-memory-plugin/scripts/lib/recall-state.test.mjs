import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { topScoreFromContextBlock } from "./recall-state.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function runAutoRecall(input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "..", "auto-recall.mjs")], {
      env: { ...cleanEnv, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`auto-recall exited ${code}: ${stderr}`));
        return;
      }
      resolve(stdout);
    });
    child.stdin.end(JSON.stringify(input));
  });
}

test("topScoreFromContextBlock returns the highest valid score", () => {
  const block = [
    "<openviking-context>",
    '<memory uri="viking://user/default/memories/a.md" score="0.42">',
    '<memory uri="viking://user/default/memories/b.md" score="invalid">',
    '<memory uri="viking://user/default/memories/c.md" score="0.69">',
    "</openviking-context>",
  ].join("\n");

  assert.equal(topScoreFromContextBlock(block), 0.69);
});

test("topScoreFromContextBlock returns zero without a valid score", () => {
  assert.equal(topScoreFromContextBlock("<openviking-context />"), 0);
  assert.equal(topScoreFromContextBlock('<memory score="invalid">'), 0);
});

test("server-assembled recall persists the highest score", async () => {
  const openVikingHome = await mkdtemp(join(tmpdir(), "ov-claude-recall-state-"));
  const server = http.createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    if (req.method === "GET" && req.url === "/health") {
      res.end(JSON.stringify({ status: "ok", result: { ok: true } }));
      return;
    }
    if (req.method === "POST" && req.url === "/api/v1/search/search") {
      res.end(JSON.stringify({
        status: "ok",
        result: {
          entries: [
            { uri: "viking://user/default/memories/a.md", score: 0.42 },
            { uri: "viking://user/default/memories/b.md", score: 0.69 },
          ],
          rendered: [
            '<memory uri="viking://user/default/memories/a.md" score="0.42">A</memory>',
            '<memory uri="viking://user/default/memories/b.md" score="0.69">B</memory>',
          ].join("\n"),
          digest: "",
          stats: { returned: 2, used_tokens: 20 },
        },
      }));
      return;
    }
    res.statusCode = 404;
    res.end(JSON.stringify({ status: "error", error: "not found" }));
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    await runAutoRecall(
      { prompt: "use prior context", session_id: "claude:top-score" },
      {
        OPENVIKING_AUTO_RECALL: "1",
        OPENVIKING_CONFIG_FILE: join(openVikingHome, "missing-ov.conf"),
        OPENVIKING_CLI_CONFIG_FILE: join(openVikingHome, "missing-ovcli.conf"),
        OPENVIKING_HOME: openVikingHome,
        OPENVIKING_MEMORY_ENABLED: "1",
        OPENVIKING_MIN_QUERY_LENGTH: "1",
        OPENVIKING_RECALL_COMPRESS: "0",
        OPENVIKING_SCORE_THRESHOLD: "0",
        OPENVIKING_STATE_DIR: join(openVikingHome, "state"),
        OPENVIKING_URL: `http://127.0.0.1:${port}`,
        OPENVIKING_WORKSPACE_PEER: "0",
      },
    );

    const state = JSON.parse(
      await readFile(join(openVikingHome, "state", "last-recall.json"), "utf-8"),
    );
    assert.equal(state.top_score, 0.69);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(openVikingHome, { recursive: true, force: true });
  }
});
