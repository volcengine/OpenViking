#!/usr/bin/env node

console.error(
  "[tos-release-client] Simplified TOS protocol is now handled by scripts/release-to-tos.sh " +
    "and scripts/upload_tos.py. The previous multi-version Node.js release client is deprecated.",
);
process.exit(1);
