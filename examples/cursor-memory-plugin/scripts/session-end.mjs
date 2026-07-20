#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "sessionEnd";
await import("./cursor-hook.mjs");
