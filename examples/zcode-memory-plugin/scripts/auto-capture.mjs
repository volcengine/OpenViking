#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "stop";
process.env.OPENVIKING_HOOK_SOURCE ||= process.argv[2] || "zcode";
await import("./zcode-hook.mjs");
