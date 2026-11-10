#!/usr/bin/env node

import { runSetupWizard } from "../lib/shared/setup-wizard.mjs"

runSetupWizard().catch((err) => {
  process.stderr.write(`${err?.stack || err}\n`)
  process.exit(1)
})
