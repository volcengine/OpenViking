import test from "node:test"
import assert from "node:assert/strict"
import { execFile } from "node:child_process"
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..")
const installer = join(repoRoot, "examples", "memory-plugin-shared", "install.sh")

async function withTempDir(prefix, fn) {
  const dir = await mkdtemp(join(tmpdir(), prefix))
  try {
    return await fn(dir)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

function runInstaller(args, options) {
  return new Promise((resolve, reject) => {
    execFile("bash", [installer, ...args], options, (error, stdout, stderr) => {
      if (error) {
        error.stdout = stdout
        error.stderr = stderr
        reject(error)
      } else {
        resolve({ stdout, stderr })
      }
    })
  })
}

test("OpenCode installer preserves JSONC comments while adding MCP config", async () => {
  await withTempDir("ov-opencode-jsonc-", async (dir) => {
    const home = join(dir, "home")
    const bin = join(dir, "bin")
    const configDir = join(home, ".config", "opencode")
    await mkdir(bin, { recursive: true })
    await mkdir(configDir, { recursive: true })

    const opencode = join(bin, "opencode")
    await writeFile(opencode, "#!/usr/bin/env sh\nprintf 'opencode 0.0.0-test\\n'\n")
    await chmod(opencode, 0o755)

    const configPath = join(configDir, "opencode.jsonc")
    await writeFile(configPath, [
      "{",
      "  // keep this user note",
      "  \"theme\": \"system\",",
      "  /* keep this block note */",
      "  \"mcp\": {",
      "    // keep this mcp note",
      "    \"other\": { \"type\": \"local\", \"command\": [\"node\", \"other.js\"] }",
      "  }",
      "}",
      "",
    ].join("\n"))

    await runInstaller([
      "--harness", "opencode",
      "--source", "dev",
      "--dist", "github",
      "--lang", "en",
      "--url", "http://127.0.0.1:1933",
      "--api-key", "",
      "--yes",
    ], {
      cwd: repoRoot,
      env: {
        ...process.env,
        HOME: home,
        OPENVIKING_HOME: join(home, ".openviking"),
        PATH: `${bin}:${process.env.PATH}`,
      },
    })

    const raw = await readFile(configPath, "utf8")
    assert.match(raw, /keep this user note/)
    assert.match(raw, /keep this block note/)
    assert.match(raw, /keep this mcp note/)
    assert.match(raw, /"theme": "system"/)
    assert.match(raw, /"other": \{ "type": "local"/)
    assert.match(raw, /"openviking"/)
    assert.match(raw, /servers\/mcp-proxy\.mjs/)
  })
})
