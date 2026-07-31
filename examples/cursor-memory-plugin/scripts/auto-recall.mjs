#!/usr/bin/env node

process.env.OPENVIKING_HOOK_EVENT = "beforeSubmitPrompt";
await import("./cursor-hook.mjs");
