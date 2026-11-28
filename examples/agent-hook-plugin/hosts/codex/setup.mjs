#!/usr/bin/env node

/**
 * Standalone ovcli.conf setup for marketplace installs of the Codex plugin:
 *
 *   node hosts/codex/setup.mjs
 */

import { runSetupWizard } from "../../lib/setup-wizard.mjs";

runSetupWizard().catch((err) => {
  process.stderr.write(`${err?.stack || err}\n`);
  process.exit(1);
});
