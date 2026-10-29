#!/usr/bin/env node

/**
 * Client-side diagnostics for the OpenCode plugin.
 *
 * OpenCode loads this plugin one of two ways: as a file plugin, the wrapper the
 * installer drops in ~/.config/opencode/plugins/, which imports the plugin
 * directory ~/.config/opencode/openviking-plugin (kept outside plugins/ because
 * OpenCode v2 also loads every directory in there),
 * or as the npm package named in opencode.json's `plugin` list. The plugin then
 * registers its MCP server and bundled skills from inside OpenCode's config
 * hook, so the host's config only matters when it switches that server off.
 *
 * Usage:
 *   node ov-memory-doctor.mjs [--json] [--offline] [--timeout <ms>] [--no-color]
 *
 * Exit code 1 when any check fails, 0 otherwise. Never prints a full api key.
 */

import { readFileSync, realpathSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve as resolvePath } from "node:path"
import { fileURLToPath } from "node:url"

import { loadConfig, resolveDataDir } from "../lib/config.mjs"
import {
  credentialSources,
  existsPath,
  fmtAge,
  fmtBytes,
  homeShort,
  inspectConfigFiles,
  reportCredentials,
  reportPeer,
  reportTimeouts,
  reportToggles,
  runDoctor,
  scanDebugLog,
  sweepEnv,
  tryJson,
} from "../lib/shared/doctor-core.mjs"

const PLUGIN_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), "..")
const REQUIRED_PLUGIN_FILES = ["index.mjs", "package.json", "servers/mcp-proxy.mjs", "lib/shared/plugin-config.mjs"]
const SKILLS = ["openviking-memory", "openviking-skills", "ov-experience-memory", "openviking-rule-search", "ov-memory-doctor"]
const NPM_PACKAGE = "@openviking/opencode-plugin"
const INSTALL_FIX = "curl -fsSL https://openviking.ai/install | bash -s -- --harness opencode"

// The installer writes these paths under $HOME regardless of XDG_CONFIG_HOME.
const opencodeDir = () => join(homedir(), ".config", "opencode")
const filePluginDir = () => join(opencodeDir(), "openviking-plugin")
// Where installers before 0.5.35 put the plugin directory.
const legacyPluginDir = () => join(opencodeDir(), "plugins", "openviking")

// The same choice install.sh's opencode_config_file makes.
function opencodeConfigPath() {
  const json = join(opencodeDir(), "opencode.json")
  const jsonc = join(opencodeDir(), "opencode.jsonc")
  let jsoncText = ""
  try {
    jsoncText = readFileSync(jsonc, "utf8")
  } catch {}
  if (jsoncText.includes('"plugin"')) return jsonc
  if (existsPath(json) || !existsPath(jsonc)) return json
  return jsonc
}

function readJsonc(path) {
  let raw
  try {
    raw = readFileSync(path, "utf8")
  } catch {
    return { exists: false }
  }
  // Drop comments outside strings and trailing commas; OpenCode accepts both.
  const text = raw.replace(/("(?:\\.|[^"\\])*")|\/\/[^\n]*|\/\*[\s\S]*?\*\//g, (match, str) => str ?? "")
    .replace(/,(\s*[}\]])/g, "$1")
  try {
    return { exists: true, ok: true, data: JSON.parse(text) }
  } catch (error) {
    return { exists: true, ok: false, error: error.message }
  }
}

const loadOpencodeConfig = () => loadConfig(PLUGIN_ROOT, process.cwd())

function checkInstall(report) {
  report.section("Plugin install")
  const manifest = tryJson(join(PLUGIN_ROOT, "package.json"))
  report.info(`running from ${homeShort(PLUGIN_ROOT)} (version ${manifest?.version || "?"})`)

  const missing = REQUIRED_PLUGIN_FILES.filter((rel) => !existsPath(join(PLUGIN_ROOT, rel)))
  if (missing.length) report.fail("plugin files missing", missing.join(", "), INSTALL_FIX)
  else report.ok("plugin files present (entry, MCP proxy, shared runtime)")

  const configPath = opencodeConfigPath()
  const hostConfig = readJsonc(configPath)
  if (hostConfig.exists && !hostConfig.ok) report.fail(`${homeShort(configPath)} cannot be parsed`, hostConfig.error, "fix the file; OpenCode refuses to start on it")
  const data = hostConfig.ok ? hostConfig.data : {}
  // A plugin entry is a spec, a [spec, options] pair, or an object naming the package.
  const specs = [data.plugin, data.plugins].flat()
    .map((spec) => (Array.isArray(spec) ? spec[0] : spec?.package ?? spec))
    .filter((spec) => typeof spec === "string")
  const viaNpm = specs.some((spec) => spec === NPM_PACKAGE || spec.startsWith(`${NPM_PACKAGE}@`))
  const wrapper = join(opencodeDir(), "plugins", "openviking.js")
  const viaFile = existsPath(wrapper)
  if (viaFile && !existsPath(join(filePluginDir(), "index.mjs"))) {
    report.fail(`${homeShort(wrapper)} loads a plugin that is not on disk`, `${homeShort(filePluginDir())}/index.mjs is missing`, INSTALL_FIX)
  } else if (viaFile && viaNpm) {
    report.warn("OpenCode loads this plugin twice", `${homeShort(wrapper)} and ${NPM_PACKAGE} in ${homeShort(configPath)}; every hook runs twice`, `remove ${NPM_PACKAGE} from the plugin list, or re-run the installer`)
  } else if (viaFile && existsPath(legacyPluginDir())) {
    report.warn("OpenCode v2 loads this plugin twice", `${homeShort(wrapper)} and the old directory ${homeShort(legacyPluginDir())}; one MCP server always fails`, `re-run the installer, or rm -rf ${homeShort(legacyPluginDir())}`)
  } else if (viaFile) report.ok(`file plugin ${homeShort(wrapper)} → ${homeShort(filePluginDir())}`)
  else if (viaNpm) report.ok(`npm plugin ${NPM_PACKAGE} listed in ${homeShort(configPath)}`)
  else report.fail("OpenCode does not load this plugin", `no ${homeShort(wrapper)} and no ${NPM_PACKAGE} in ${homeShort(configPath)}`, INSTALL_FIX)

  // The plugin registers the server itself, but leaves one switched off alone.
  const server = data.mcp?.openviking
  const cfg = loadOpencodeConfig()
  if (!cfg.mcpEnabled) report.info("hook-only mode (mcpEnabled false): no MCP server and no bundled skills are registered")
  else if (server?.enabled === false || server?.disabled === true) {
    report.warn(`the 'openviking' MCP server is switched off in ${homeShort(configPath)}`, "the plugin does not re-enable it, so the memory tools and bundled skills are missing", "remove the enabled: false / disabled: true from that entry")
  } else report.ok("MCP server 'openviking' is registered by the plugin at startup")

  const absent = SKILLS.filter((skill) => !existsPath(join(PLUGIN_ROOT, "skills", skill, "SKILL.md")))
  if (absent.length) report.warn(`skills missing from ${homeShort(join(PLUGIN_ROOT, "skills"))}`, absent.join(", "), INSTALL_FIX)
  else report.ok(`${SKILLS.length} bundled skills`)
}

function checkConfig(report, cfg, host) {
  report.section("Configuration")
  const { cliConf, ovConf } = inspectConfigFiles(report, host)
  if (!cliConf.ok && !ovConf.ok && !(process.env.OPENVIKING_URL || process.env.OPENVIKING_BASE_URL)) {
    report.fail("no usable config — the plugin falls back to http://127.0.0.1:1933 with no key", "", "create ~/.openviking/ovcli.conf with url + api_key (chmod 600)")
  }
  const keyInfo = reportCredentials(report, cfg, credentialSources(cfg, cliConf, ovConf), { account: cfg.account, user: cfg.user })
  report.info(`auth mode ${cfg.authMode} (identity headers ${cfg.sendIdentityHeaders ? "sent" : "not sent"})`)
  const peer = reportPeer(report, cfg)
  reportTimeouts(report, cfg, host)
  if (!cfg.enabled) report.warn("the plugin is disabled", "it loads but registers no hooks", "unset OPENVIKING_MEMORY_ENABLED=0 or enabled: false in ovcli.conf plugin.opencode")
  reportToggles(report, cfg, host, [
    `auto-recall ${cfg.autoRecall ? "on" : "OFF"}`,
    `auto-capture ${cfg.autoCapture ? "on" : "OFF"}`,
    `write path ${cfg.writePathAsync ? "async" : "sync"}`,
    `MCP ${cfg.mcpEnabled ? "on" : "OFF"}`,
    `repo context ${cfg.repoContext?.enabled ? "on" : "OFF"}`,
  ])
  sweepEnv(report, cfg, host)
  return { keyInfo, peer, ovConf }
}

function checkActivity(report, cfg, connection) {
  report.section("Recent activity")
  const dataDir = resolveDataDir(PLUGIN_ROOT, cfg)
  const statePath = join(dataDir, "openviking-session-state.json")
  const state = tryJson(statePath)
  if (!state) report.info(`no session state in ${homeShort(dataDir)} yet — the plugin has not run a session (or dataDir points elsewhere)`)
  else report.info(`${Object.keys(state.sessions || {}).length} tracked session(s) in ${homeShort(statePath)}; last saved ${fmtAge(state.lastSaved)}`)

  // The plugin's own log is always written; the shared modules' debug log only
  // when debug is on.
  const pluginLog = join(dataDir, "openviking-memory.log")
  let lines = []
  try {
    lines = readFileSync(pluginLog, "utf8").slice(-512 * 1024).split("\n")
  } catch {
    report.info(`no plugin log at ${homeShort(pluginLog)} — OpenCode has not loaded the plugin since it was installed`)
  }
  const entries = lines.map((line) => {
    try {
      return JSON.parse(line)
    } catch {
      return null
    }
  }).filter(Boolean)
  if (entries.length) {
    const last = Date.parse(entries.at(-1).timestamp) || 0
    report.info(`plugin log ${homeShort(pluginLog)} — last entry ${fmtAge(last)}`)
    const recent = entries.filter((e) => e.level === "ERROR" && Date.parse(e.timestamp) >= last - 24 * 60 * 60 * 1000).slice(-5)
    if (recent.length) report.warn(`plugin errors in the last day of logging (${recent.length} shown)`, recent.map((e) => `${e.timestamp} ${e.tool}: ${e.message}${e.data?.error ? ` (${e.data.error})` : ""}`).join("\n"))
  }

  const log = scanDebugLog(cfg.debugLogPath)
  if (log.exists) {
    report.info(`debug log ${homeShort(log.path)} — ${fmtBytes(log.size)}, last write ${fmtAge(log.mtimeMs)}`)
    if (log.recentErrors.length) report.warn(`debug-log errors in the last day of logging (${log.recentErrors.length} shown)`, log.recentErrors.map((e) => `${e.ts} ${e.hook}/${e.stage}: ${e.message}`).join("\n"))
  }
  if (connection?.summary?.reachable === false) report.info("captures that fail are queued on disk and replayed at the next session start")
}

const HOST = {
  harness: "opencode",
  pluginRoot: PLUGIN_ROOT,
  cliName: "opencode",
  launcherHint: "OpenCode",
  loadConfig: loadOpencodeConfig,
  checkInstall,
  checkConfig,
  checkActivity,
  resolveIdentity: (cfg) => ({ account: cfg.account, user: cfg.user }),
  extraResolved: (cfg) => ({ credentialSource: cfg.credentialSource, authMode: cfg.authMode, dataDir: resolveDataDir(PLUGIN_ROOT, cfg) }),
}

function isDirectRun() {
  if (!process.argv[1]) return false
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))
  } catch {
    return resolvePath(process.argv[1]) === fileURLToPath(import.meta.url)
  }
}

if (isDirectRun()) {
  runDoctor(HOST).catch((err) => {
    console.error("ov-memory-doctor failed:", err?.stack || err?.message || err)
    process.exit(2)
  })
}
