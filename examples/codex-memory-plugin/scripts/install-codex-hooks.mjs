#!/usr/bin/env node

import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve as resolvePath } from "node:path"
import { fileURLToPath } from "node:url"
import { loadConfig } from "./config.mjs"

const HOOK_TAG = "openviking-codex-memory-plugin"
const pluginRoot = resolvePath(dirname(fileURLToPath(import.meta.url)), "..")
const codexHome = resolvePath((process.env.CODEX_HOME || join(homedir(), ".codex")).replace(/^~/, homedir()))
const hooksPath = join(codexHome, "hooks.json")
const cfg = loadConfig()

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`
}

function buildCommand(scriptName) {
  return `node ${shellQuote(join(pluginRoot, "scripts", scriptName))}`
}

function loadHooksFile() {
  try {
    return JSON.parse(readFileSync(hooksPath, "utf-8"))
  } catch {
    return { hooks: {} }
  }
}

function ensureEvent(hooksFile, eventName) {
  if (!Array.isArray(hooksFile.hooks[eventName])) hooksFile.hooks[eventName] = []
  return hooksFile.hooks[eventName]
}

function managedCommands() {
  return new Set([
    buildCommand("bootstrap-runtime.mjs"),
    buildCommand("auto-recall.mjs"),
    buildCommand("auto-capture.mjs"),
  ])
}

function isManagedHook(hook) {
  if (typeof hook?.command === "string" && managedCommands().has(hook.command)) return true
  return typeof hook?.statusMessage === "string" && hook.statusMessage.startsWith(HOOK_TAG)
}

function stripManagedHooks(groups) {
  return groups
    .map((group) => ({
      ...group,
      hooks: Array.isArray(group.hooks)
        ? group.hooks.filter((hook) => !isManagedHook(hook))
        : [],
    }))
    .filter((group) => group.hooks.length > 0)
}

function upsertHook(hooksFile, eventName, hook) {
  const existing = stripManagedHooks(ensureEvent(hooksFile, eventName))
  existing.push({ hooks: [hook] })
  hooksFile.hooks[eventName] = existing
}

const hooksFile = loadHooksFile()
if (!hooksFile.hooks || typeof hooksFile.hooks !== "object") hooksFile.hooks = {}

hooksFile.hooks.SessionStart = stripManagedHooks(Array.isArray(hooksFile.hooks.SessionStart) ? hooksFile.hooks.SessionStart : [])
if (hooksFile.hooks.SessionStart.length === 0) {
  delete hooksFile.hooks.SessionStart
}

upsertHook(hooksFile, "UserPromptSubmit", {
  type: "command",
  command: buildCommand("auto-recall.mjs"),
  timeout: 30,
})

hooksFile.hooks.Stop = stripManagedHooks(Array.isArray(hooksFile.hooks.Stop) ? hooksFile.hooks.Stop : [])
if (hooksFile.hooks.Stop.length === 0) {
  delete hooksFile.hooks.Stop
}

if (cfg.autoCapture) {
  upsertHook(hooksFile, "Stop", {
    type: "command",
    command: buildCommand("auto-capture.mjs"),
    timeout: 45,
  })
}

mkdirSync(dirname(hooksPath), { recursive: true })
writeFileSync(hooksPath, `${JSON.stringify(hooksFile, null, 2)}\n`)
process.stdout.write(`Installed OpenViking Codex hooks into ${hooksPath} (mode=${cfg.mode})\n`)
