import { loadConfig } from "../config.mjs";

function setting(value, fallback) {
  return String(value ?? fallback).trim().toLowerCase();
}

export function usageView(cfg = loadConfig()) {
  return setting(cfg.usageView, "summary");
}

export function usageEnabled(cfg = loadConfig()) {
  return !["off", "false", "0", "disabled"].includes(usageView(cfg));
}

// Clients do not expose a universal desktop/terminal identifier to hooks.
// An explicit choice takes precedence; terminal environments use native output.
export function usageOutput(cfg = loadConfig()) {
  const value = setting(cfg.usageOutput, "auto");
  if (["terminal", "desktop"].includes(value)) return value;
  const term = String(process.env.TERM || "").trim();
  return process.env.TERM_PROGRAM || (term && term !== "dumb") ? "terminal" : "desktop";
}
