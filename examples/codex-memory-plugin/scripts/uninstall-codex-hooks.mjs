#!/usr/bin/env node

import { existsSync, readFileSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve as resolvePath } from "node:path"
import { fileURLToPath } from "node:url"

const HOOK_TAG = "openviking-codex-memory-plugin"
const pluginRoot = resolvePath(dirname(fileURLToPath(import.meta.url)), "..")
const codexHome = resolvePath((process.env.CODEX_HOME || join(homedir(), ".codex")).replace(/^~/, homedir()))
const hooksPath = join(codexHome, "hooks.json")

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`
}

function buildCommand(scriptName) {
  return `node ${shellQuote(join(pluginRoot, "scripts", scriptName))}`
}

const managedCommands = new Set([
  buildCommand("bootstrap-runtime.mjs"),
  buildCommand("auto-recall.mjs"),
  buildCommand("auto-capture.mjs"),
])

function isManagedHook(hook) {
  if (typeof hook?.command === "string" && managedCommands.has(hook.command)) return true
  return typeof hook?.statusMessage === "string" && hook.statusMessage.startsWith(HOOK_TAG)
}

if (!existsSync(hooksPath)) {
  process.stdout.write(`No hooks file found at ${hooksPath}\n`)
  process.exit(0)
}

let hooksFile
try {
  hooksFile = JSON.parse(readFileSync(hooksPath, "utf-8"))
} catch (err) {
  process.stderr.write(`Failed to parse ${hooksPath}: ${err instanceof Error ? err.message : String(err)}\n`)
  process.exit(1)
}

if (!hooksFile.hooks || typeof hooksFile.hooks !== "object") hooksFile.hooks = {}

for (const eventName of ["SessionStart", "UserPromptSubmit", "Stop"]) {
  const groups = Array.isArray(hooksFile.hooks[eventName]) ? hooksFile.hooks[eventName] : []
  hooksFile.hooks[eventName] = groups
    .map((group) => ({
      ...group,
      hooks: Array.isArray(group.hooks)
        ? group.hooks.filter((hook) => !isManagedHook(hook))
        : [],
    }))
    .filter((group) => group.hooks.length > 0)
}

writeFileSync(hooksPath, `${JSON.stringify(hooksFile, null, 2)}\n`)
process.stdout.write(`Removed OpenViking Codex hooks from ${hooksPath}\n`)
