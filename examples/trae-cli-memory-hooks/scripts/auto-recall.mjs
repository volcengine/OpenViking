#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "user-prompt-submit";
await import("./trae-cli-hook.mjs");
