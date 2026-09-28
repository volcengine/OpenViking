#!/usr/bin/env node

import { preToolUseOutput, runUriGuardHook } from "./shared/uri-guard.mjs";

export const evaluatePreToolUse = (input = {}) => preToolUseOutput(input);

runUriGuardHook(import.meta.url, evaluatePreToolUse);
