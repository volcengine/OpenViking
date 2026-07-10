#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "preCompact";
await import("./cursor-hook.mjs");
