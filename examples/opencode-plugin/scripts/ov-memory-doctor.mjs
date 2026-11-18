#!/usr/bin/env node
import { fileURLToPath } from "node:url"
import { loadConfig } from "../lib/config.mjs"
import { diagnoseOpenCode } from "../lib/diagnostics.mjs"
import { parseArgs } from "../lib/shared/doctor-core.mjs"

const pluginRoot = fileURLToPath(new URL("../", import.meta.url))
const opts = parseArgs(process.argv.slice(2))
const result = await diagnoseOpenCode({ config: loadConfig(pluginRoot, process.cwd(), { readOnly: true }), pluginRoot, offline: opts.offline, timeoutMs: opts.timeoutMs })
console.log(opts.json ? JSON.stringify(result, null, 2) : result.text)
process.exitCode = result.exitCode
