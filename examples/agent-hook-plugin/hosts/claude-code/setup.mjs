#!/usr/bin/env node

/**
 * Standalone ovcli.conf setup for marketplace installs of the Claude Code plugin:
 *
 *   node hosts/claude-code/setup.mjs
 */

import { runSetupWizard } from "../../lib/setup-wizard.mjs";

runSetupWizard().catch((err) => {
  process.stderr.write(`${err?.stack || err}\n`);
  process.exit(1);
});
