#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "session-start";
await import("./trae-cli-hook.mjs");
