export function usageEnabled() {
  return !["off", "false", "0", "disabled"].includes(String(process.env.OPENVIKING_USAGE_VIEW || "summary").trim().toLowerCase());
}

// Clients do not expose a universal desktop/terminal identifier to hooks.
// An explicit choice takes precedence; terminal environments use native output.
export function usageOutput() {
  const value = String(process.env.OPENVIKING_USAGE_OUTPUT || "auto").trim().toLowerCase();
  if (["terminal", "desktop"].includes(value)) return value;
  const term = String(process.env.TERM || "").trim();
  return process.env.TERM_PROGRAM || (term && term !== "dumb") ? "terminal" : "desktop";
}
