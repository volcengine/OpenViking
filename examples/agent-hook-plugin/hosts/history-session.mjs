/**
 * `session_history` arguments that leave `session` out, or null when the model
 * passed one (kept as is, like the MCP proxy keeps it) or the arguments are not
 * an object this hook can extend.
 */
export function withoutHistorySession(args) {
  if (!args || typeof args !== "object" || Array.isArray(args)) return null;
  const given = args.session;
  return given == null || (typeof given === "string" && !given.trim()) ? args : null;
}
