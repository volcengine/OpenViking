#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "session-start";
await import("./zcode-hook.mjs");
