import type { Register } from "claude-code";
import { registerSeal } from "./seal/register";
import { register as registerUsage } from "./usage/register";

// Claude Code loads one hooks module per plugin; this one gathers the mods.
export const register: Register = (on, options) => {
  registerSeal(on);
  registerUsage(on, options);
};
