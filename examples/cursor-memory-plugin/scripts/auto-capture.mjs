#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "stop";
await import("./cursor-hook.mjs");
