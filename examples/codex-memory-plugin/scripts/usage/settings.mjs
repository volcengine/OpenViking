export function usageEnabled() {
  return !["off", "false", "0", "disabled"].includes(String(process.env.OPENVIKING_USAGE_VIEW || "summary").trim().toLowerCase());
}
