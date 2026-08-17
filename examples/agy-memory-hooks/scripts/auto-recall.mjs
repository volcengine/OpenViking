#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "pre-invocation";
await import("./agy-hook.mjs");
