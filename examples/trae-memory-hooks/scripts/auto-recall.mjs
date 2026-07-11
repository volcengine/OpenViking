#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "user-prompt-submit";
process.env.OPENVIKING_HOOK_SOURCE ||= process.argv[2] || "trae";
await import("./trae-hook.mjs");
