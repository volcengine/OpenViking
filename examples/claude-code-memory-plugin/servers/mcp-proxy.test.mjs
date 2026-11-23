import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  expectExit,
  readRequestBody,
  runHookScript,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeCredentialFiles,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";

const PLUGIN = join(dirname(fileURLToPath(import.meta.url)), "..");

// Claude Code is the parent of both its hooks and its MCP server; here the test
// process plays it, so the proxy's parent pid is this process's pid.
test("session_history follows the Claude Code session across /clear", async () => {
  const restore = scrubOpenVikingEnv();
  const files = await writeCredentialFiles("ov-cc-proxy-", {});
  const upstream = [];
  try {
    await withMockOpenViking(async (req, res) => {
      if (req.method === "POST" && new URL(req.url, "http://localhost").pathname === "/mcp") {
        const body = await readRequestBody(req);
        upstream.push(body.params);
        writeJson(res, { jsonrpc: "2.0", id: body.id, result: { content: [] } });
        return;
      }
      writeJson(res, { status: "ok", result: [] });
    }, async (baseUrl) => {
      await writeFile(files.cliPath, JSON.stringify({ url: baseUrl }));
      const env = {
        ...process.env,
        ...files.env,
        OPENVIKING_PENDING_DIR: join(files.dir, "pending"),
        OPENVIKING_TIMEOUT_MS: "2000",
        CLAUDE_CODE_SESSION_ID: "first",
      };
      const proxy = spawn(process.execPath, [join(PLUGIN, "servers", "mcp-proxy.mjs")], {
        env,
        stdio: ["pipe", "pipe", "inherit"],
      });
      const responses = [];
      createInterface({ input: proxy.stdout }).on("line", (line) => responses.push(JSON.parse(line)));
      let nextId = 1;
      const call = async (args) => {
        const id = nextId++;
        const params = { name: "session_history", arguments: args };
        proxy.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method: "tools/call", params })}\n`);
        const deadline = Date.now() + 5000;
        while (!responses.some((r) => r.id === id)) {
          assert.ok(Date.now() < deadline, `no response to call ${id}`);
          await new Promise((r) => setTimeout(r, 10));
        }
        return upstream.at(-1).arguments;
      };
      const sessionStart = async (sessionId, source, pid = process.pid) => expectExit(await runHookScript(join(PLUGIN, "scripts", "session-start.mjs"), {
        input: { session_id: sessionId, source, cwd: files.dir },
        cwd: files.dir,
        env: { ...env, CLAUDE_PID: String(pid), OPENVIKING_NO_AUTO_INJECT: "1" },
      }));
      try {
        await sessionStart("first", "startup");
        assert.deepEqual(await call({ action: "overview" }), { action: "overview", session: "cc-first" });
        await sessionStart("second", "clear");
        assert.deepEqual(await call({ action: "overview" }), { action: "overview", session: "cc-second" });
        // Another Claude Code process starting a session does not move this one.
        await sessionStart("other", "startup", process.pid + 100000);
        assert.deepEqual(await call({ action: "overview" }), { action: "overview", session: "cc-second" });
        assert.deepEqual(
          await call({ action: "overview", session: "cc-first" }),
          { action: "overview", session: "cc-first" },
        );
      } finally {
        proxy.stdin.end();
        await new Promise((resolve) => proxy.on("close", resolve));
      }
    });
  } finally {
    restore();
    await rm(files.dir, { recursive: true, force: true });
  }
});
