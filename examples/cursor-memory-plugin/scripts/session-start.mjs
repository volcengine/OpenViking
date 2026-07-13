#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "sessionStart";
await import("./cursor-hook.mjs");
