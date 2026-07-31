#!/usr/bin/env node

import { runSetupWizard } from "../shared/setup-wizard.mjs";

runSetupWizard().catch((err) => {
  process.stderr.write(`${err?.stack || err}\n`);
  process.exit(1);
});
