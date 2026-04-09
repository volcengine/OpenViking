#!/usr/bin/env node

import { loadConfig } from "./config.mjs"
import { createLogger } from "./debug-log.mjs"
import { drainCaptureQueue } from "./capture-queue.mjs"

const cfg = loadConfig()
const { log, logError } = createLogger("capture-worker")

async function main() {
  await drainCaptureQueue(cfg, log, logError)
}

main().catch((err) => {
  logError("uncaught", err)
  process.exit(0)
})
