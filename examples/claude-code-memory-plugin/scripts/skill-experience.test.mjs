import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8");
      try {
        resolve(raw ? JSON.parse(raw) : null);
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

function writeJson(res, value) {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}

async function withMockOpenViking(handler, fn) {
  const server = http.createServer((req, res) => {
    handler(req, res).catch((err) => {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: String(err?.stack || err) }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    return await fn(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function runSkillExperience(input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(process.execPath, [join(SCRIPT_DIR, "skill-experience.mjs")], {
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
        reject(new Error(`skill-experience exited ${code}: ${stderr}`));
        return;
      }
      resolve({ stdout, stderr });
    });
    child.stdin.end(JSON.stringify(input));
  });
}

test("skill experience hook is disabled by default", async () => {
  const tmp = await mkdtemp(join(tmpdir(), "ov-skill-exp-disabled-"));
  try {
    const skillDir = join(tmp, "skills", "demo");
    await mkdir(skillDir, { recursive: true });
    const skillPath = join(skillDir, "SKILL.md");
    await writeFile(skillPath, "# Demo Skill\n");

    const result = await runSkillExperience(
      { tool_name: "Read", tool_input: { file_path: skillPath } },
      {
        OPENVIKING_MEMORY_ENABLED: "1",
        OPENVIKING_CONFIG_FILE: join(tmp, "missing-ov.conf"),
        OPENVIKING_CLI_CONFIG_FILE: join(tmp, "missing-ovcli.conf"),
      },
    );
    assert.deepEqual(JSON.parse(result.stdout.trim()), { decision: "approve" });
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
});

test("skill experience hook ignores non-skill files without network access", async () => {
  const tmp = await mkdtemp(join(tmpdir(), "ov-skill-exp-nonskill-"));
  let requests = 0;
  try {
    await withMockOpenViking(async (_req, res) => {
      requests += 1;
      writeJson(res, { status: "ok", result: {} });
    }, async (baseUrl) => {
      const result = await runSkillExperience(
        { tool_name: "Read", tool_input: { file_path: join(tmp, "README.md") } },
        {
          OPENVIKING_MEMORY_ENABLED: "1",
          OPENVIKING_SKILL_EXPERIENCE: "1",
          OPENVIKING_CONFIG_FILE: join(tmp, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(tmp, "missing-ovcli.conf"),
          OPENVIKING_URL: baseUrl,
        },
      );
      assert.deepEqual(JSON.parse(result.stdout.trim()), { decision: "approve" });
    });
    assert.equal(requests, 0);
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
});

test("skill experience hook injects matching experience memories", async () => {
  const tmp = await mkdtemp(join(tmpdir(), "ov-skill-exp-enabled-"));
  const requests = [];
  try {
    const skillDir = join(tmp, "skills", "demo");
    await mkdir(skillDir, { recursive: true });
    const skillPath = join(skillDir, "SKILL.md");
    await writeFile(skillPath, "---\nname: Demo Skill\n---\n# Demo Skill\n");

    await withMockOpenViking(async (req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (req.method === "POST" && url.pathname === "/api/v1/search/find") {
        const body = await readRequestBody(req);
        requests.push(body);
        writeJson(res, {
          status: "ok",
          result: {
            memories: [{
              uri: "viking://user/default/memories/experiences/demo-skill.md",
              score: 0.88,
              abstract: "Use the demo skill after checking prerequisites.",
            }],
          },
        });
        return;
      }
      writeJson(res, { status: "ok", result: {} });
    }, async (baseUrl) => {
      const result = await runSkillExperience(
        { tool_name: "Read", tool_input: { file_path: skillPath } },
        {
          OPENVIKING_MEMORY_ENABLED: "1",
          OPENVIKING_SKILL_EXPERIENCE: "1",
          OPENVIKING_CONFIG_FILE: join(tmp, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(tmp, "missing-ovcli.conf"),
          OPENVIKING_URL: baseUrl,
        },
      );
      const output = JSON.parse(result.stdout.trim());
      assert.equal(output.decision, "approve");
      assert.equal(output.hookSpecificOutput.hookEventName, "PostToolUse");
      assert.match(output.hookSpecificOutput.additionalContext, /Demo Skill/);
      assert.match(output.hookSpecificOutput.additionalContext, /checking prerequisites/);
    });

    assert.equal(requests.length, 1);
    assert.equal(requests[0].target_uri, "viking://user/memories/experiences");
    assert.equal(requests[0].limit, 3);
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
});
