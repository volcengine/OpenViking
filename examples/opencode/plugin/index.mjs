import { exec } from "child_process"
import { promisify } from "util"
import { readFileSync, mkdirSync, writeFileSync, existsSync } from "fs"
import { homedir } from "os"
import { join, dirname } from "path"
import { fileURLToPath } from "url"

const execAsync = promisify(exec)
const __dirname = dirname(fileURLToPath(import.meta.url))
const OV_CONF = join(homedir(), ".openviking", "ov.conf")

// ── Helpers ───────────────────────────────────────────────────────────────────

async function run(cmd, opts = {}) {
  return execAsync(cmd, { timeout: 10000, ...opts })
}

async function isHealthy() {
  try {
    await run("ov health", { timeout: 3000 })
    return true
  } catch {
    return false
  }
}

async function startServer() {
  // Start in background, wait up to 30s for healthy
  await run("openviking-server --config " + OV_CONF + " > /tmp/openviking.log 2>&1 &")
  for (let i = 0; i < 10; i++) {
    await new Promise((r) => setTimeout(r, 3000))
    if (await isHealthy()) return true
  }
  return false
}

// ── Skill auto-install ────────────────────────────────────────────────────────

function installSkill() {
  const src = join(__dirname, "skills", "openviking", "SKILL.md")
  const dest = join(homedir(), ".config", "opencode", "skills", "openviking", "SKILL.md")
  try {
    if (!existsSync(dirname(dest))) mkdirSync(dirname(dest), { recursive: true })
    const content = readFileSync(src, "utf8")
    if (!existsSync(dest) || readFileSync(dest, "utf8") !== content) {
      writeFileSync(dest, content, "utf8")
    }
  } catch {}
}

// ── Repo context cache ────────────────────────────────────────────────────────

let cachedRepos = null
let lastFetchTime = 0
const CACHE_TTL_MS = 60 * 1000

async function loadRepos() {
  const now = Date.now()
  if (cachedRepos !== null && now - lastFetchTime < CACHE_TTL_MS) return

  try {
    const { stdout } = await run(
      "ov --output json ls viking://resources/ --abs-limit 2000"
    )
    const items = JSON.parse(stdout)?.result ?? []
    const repos = items
      .filter((item) => item.uri?.startsWith("viking://resources/"))
      .map((item) => {
        const name = item.uri.replace("viking://resources/", "").replace(/\/$/, "")
        return item.abstract
          ? `- **${name}** (${item.uri})\n  ${item.abstract}`
          : `- **${name}** (${item.uri})`
      })
    if (repos.length > 0) {
      cachedRepos = repos.join("\n")
      lastFetchTime = now
    }
  } catch {}
}

// ── Init: check deps, start server if needed ─────────────────────────────────

async function init(client) {
  const toast = (message, variant = "warning") =>
    client.tui.showToast({
      body: { title: "OpenViking", message, variant, duration: 8000 },
    }).catch(() => {})

  // 服务已在跑，直接返回
  if (await isHealthy()) return true

  // 没跑，先看是哪种情况
  try {
    await run("command -v ov", { timeout: 2000 })
  } catch {
    // command not found → 没装
    await toast("openviking 未安装，请运行: pip install openviking", "error")
    return false
  }

  // 装了但没有配置文件 → 无法启动
  if (!existsSync(OV_CONF)) {
    await toast("未找到 ~/.openviking/ov.conf，请先配置 API keys 后再启动服务", "warning")
    return false
  }

  // 装了有配置，服务没跑 → 静默自动启动
  const started = await startServer()
  if (!started) {
    await toast("openviking 服务启动失败，查看日志: /tmp/openviking.log", "error")
    return false
  }

  return true
}

// ── Plugin export ─────────────────────────────────────────────────────────────

/**
 * @type {import('@opencode-ai/plugin').Plugin}
 */
export async function OpenVikingPlugin({ client }) {
  installSkill()

  // 后台初始化，不阻塞 opencode 启动
  Promise.resolve().then(async () => {
    const ready = await init(client)
    if (ready) await loadRepos()
  })

  return {
    "experimental.chat.system.transform": (_input, output) => {
      if (!cachedRepos) return
      output.system.push(
        `## OpenViking — Indexed Code Repositories\n\n` +
        `The following repos are semantically indexed and searchable.\n` +
        `When the user asks about any of these projects or their internals, ` +
        `you MUST proactively load skill("openviking") and use the correct ov commands to search and retrieve content before answering.\n\n` +
        cachedRepos
      )
    },

    "session.created": async () => {
      const ready = await init(client)
      if (ready) {
        cachedRepos = null
        await loadRepos()
      }
    },
  }
}
