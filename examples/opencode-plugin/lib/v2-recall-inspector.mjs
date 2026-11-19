import { sanitizeDiagnosticText } from "./diagnostics.mjs"

/** Inspect only the context OpenViking already stored for this session. */
export async function inspectV2Recall(ctx, config, sessionID) {
  const view = { schemaVersion: 1, sessionID, state: "unavailable", source: null,
    turns: [], pending: null, error: null,
    limitations: ["Live pending requests, wait budgets and runtime errors are not exposed.",
      "Only stored context is shown; the latest unsaved turn may be absent."] }

  let entries
  try {
    const ledger = await ctx.storage?.get?.(`recall-ledger/${sessionID}`)
    if (ledger != null) {
      if (ledger.version !== 1 || !Array.isArray(ledger.entries) || ledger.entries.some((entry) =>
        !Array.isArray(entry) || typeof entry[0] !== "string" || typeof entry[1] !== "string")) {
        return { ...view, state: "error", error: "Unsupported or invalid replay ledger." }
      }
      entries = ledger.entries.slice(-10)
      view.source = "persisted_replay_ledger"
    } else if (ctx.session?.context) {
      const messages = await ctx.session.context({ sessionID })
      if (!Array.isArray(messages)) throw new Error("Unexpected session context")
      entries = messages.filter((message) => message.type === "user" && typeof message.id === "string" &&
        Array.isArray(message.metadata?.openviking?.context)).slice(-10).map((message) =>
        [message.id, message.metadata.openviking.context.filter((block) => typeof block === "string").join("\n\n")])
      view.source = "stored_prompt_metadata"
    }
  } catch {
    return { ...view, state: "error", error: "Cannot read stored OpenViking prompt context." }
  }
  if (!entries?.length) {
    view.limitations.push("No stored context is available for this session. This does not establish what the model received.")
    return view
  }
  let budget = 60000
  for (const [messageID, original] of entries.reverse()) {
    if (budget <= 0) break
    const redacted = sanitizeDiagnosticText(config, original)
    const text = redacted.slice(0, Math.min(12000, budget))
    if (!text.trim()) continue
    budget -= text.length
    view.turns.push({ messageID, text, redacted: redacted !== original, truncated: text.length < redacted.length,
      sources: [...new Set(text.match(/viking:\/\/[^\s<>"'`\]\)]+/g) ?? [])] })
  }
  if (view.turns.length) view.state = "available"
  view.limitations.push("At most the latest 10 persisted turns and 60,000 characters are shown; configured credentials are redacted.")
  return view
}
