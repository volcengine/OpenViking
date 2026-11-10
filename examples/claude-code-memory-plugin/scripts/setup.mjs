#!/usr/bin/env node

/**
 * Standalone ovcli.conf setup for marketplace installs of the Claude Code plugin:
 *
 *   node scripts/setup.mjs
 */

import { runSetupWizard } from "./shared/setup-wizard.mjs";

runSetupWizard().catch((err) => {
  process.stderr.write(`${err?.stack || err}\n`);
  process.exit(1);
});
