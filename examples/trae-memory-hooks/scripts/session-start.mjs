#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "session-start";
process.env.OPENVIKING_HOOK_SOURCE ||= process.argv[2] || "trae";
await import("./trae-hook.mjs");
