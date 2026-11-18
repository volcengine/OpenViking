import { OPENVIKING_CONTROLS } from "./controls-contract.mjs"

const ACTIONS = ["status", "doctor", "recall", "commit"]
const COMMIT_MESSAGES = {
  accepted: "Commit accepted. Memory extraction completion has not been verified.",
  disabled: "Capture is disabled for this session. Nothing was committed.",
  no_activity: "No captured activity for this session. Nothing was committed.",
  pending_capture: "Capture delivery is pending. Resolve it before committing again.",
  queued: "Commit queued for retry. Memory extraction has not started.",
  not_found: "The OpenViking session was not found. Nothing was committed.",
  skipped: "Commit skipped. Memory extraction has not started.",
}

// UI text must never interpret terminal controls or echo arbitrary RPC errors.
export function displayText(value) {
  return String(value ?? "").replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g, "")
}

export function statusText(report) {
  return [
    `Connection: ${report.connection.state}`,
    `Plugin: ${report.installation.state}; MCP: ${report.mcp.state}; skills: ${report.skills.state}`,
    `Capture: ${report.capture.state} (persisted activity across this location)`,
    `Pending messages: ${report.capture.pendingMessages ?? "unavailable"}`,
    `Last accepted commit: ${report.capture.lastCommitAcceptedAt ? new Date(report.capture.lastCommitAcceptedAt).toISOString() : "unavailable"}`,
    "An accepted commit does not establish completed memory extraction.",
    "Use /ov doctor for checks or /ov recall for stored context.",
  ].join("\n")
}

export function recallText(view) {
  const lines = [
    `Session: ${view.sessionID}`,
    `Recall: ${view.state}; source: ${view.source ?? "unavailable"}`,
    `Pending: ${view.pending === null ? "unavailable" : JSON.stringify(view.pending)}`,
    "Live runtime error: unavailable",
    ...(view.error ? [`Inspection error: ${view.error}`] : []),
    ...(view.limitations ?? []),
  ]
  for (const turn of view.turns ?? []) {
    lines.push("", `Turn: ${turn.messageID}${turn.truncated ? " (truncated)" : ""}${turn.redacted ? " (credentials redacted)" : ""}`,
      `Sources: ${turn.sources?.join(", ") || "not recorded"}`, turn.text)
  }
  return lines.join("\n")
}

/** TUI-local commands; every mutation requires the current session and confirmation. */
export function startTuiControls(context, { renderDialog, renderStatus, updateStatus }) {
  const rpc = context.client.rpc(OPENVIKING_CONTROLS)
  const controller = new AbortController()
  let active = false
  let disposed = false
  let generation = 0
  let dialogOwned = false
  let dialogOpen = false
  let dialogID = 0
  const location = () => context.location ?? context.data.location.default()
  const currentSession = () => {
    const route = context.ui.router.current()
    return route.type === "session" ? route.sessionID : undefined
  }
  const show = (title, text) => {
    if (disposed) return
    const id = ++dialogID
    dialogOwned = true
    dialogOpen = true
    context.ui.dialog.show(() => renderDialog(title, displayText(text)), () => {
      if (id !== dialogID) return
      dialogOpen = false
      dialogOwned = false
    })
    context.ui.dialog.set({ size: "large" })
  }
  const call = (action, input) => rpc[action](input, { location: location(), signal: controller.signal })
  const run = async (action) => {
    if (disposed || active) return
    if (!ACTIONS.includes(action)) {
      show("OpenViking", "Use /ov status, /ov doctor, /ov recall or /ov commit.")
      return
    }
    active = true
    const request = ++generation
    try {
      const sessionID = currentSession()
      if ((action === "commit" || action === "recall") && !sessionID) {
        show("OpenViking", "Open a session first. This control only applies to the current session.")
        return
      }
      if (action === "commit") {
        dialogID++
        dialogOpen = false
        dialogOwned = true
        const confirmed = await context.ui.dialog.confirm({ title: "Commit current session?",
          message: "Send eligible captured activity from this session to OpenViking for memory extraction? This writes to the server.",
          label: { confirm: "Commit", cancel: "Cancel" } })
        dialogOwned = false
        if (disposed || !confirmed) return
        if (currentSession() !== sessionID) {
          show("OpenViking", "The current session changed. Run /ov commit again in the intended session.")
          return
        }
      }
      show(`OpenViking ${action}`, "Loading…")
      updateStatus(`OV: ${action}…`)
      const result = await call(action, action === "recall" || action === "commit" ? { sessionID } : {})
      if (disposed || request !== generation) return
      updateStatus(action === "status" || action === "doctor"
        ? `OV: ${result.connection.state} · capture ${result.capture.state}` : "OV: /ov status to refresh")
      // A dismissed dialog must not reopen when its network request finishes.
      if (!dialogOpen || ((action === "recall" || action === "commit") && currentSession() !== sessionID)) return
      show(`OpenViking ${action}`, action === "status" ? statusText(result)
        : action === "doctor" ? result.text
          : action === "recall" ? recallText(result)
            : COMMIT_MESSAGES[result.status] ?? "Commit returned an unsupported result. Run /ov doctor.")
    } catch (error) {
      if (disposed || controller.signal.aborted) return
      updateStatus("OV: unavailable · /ov doctor")
      if (dialogOpen) {
        const kind = ["operation_failed", "rpc.not_found", "rpc.method_not_found"].includes(error?.type) ? ` (${error.type})` : ""
        show("OpenViking unavailable", `The OpenViking control could not complete${kind}. Check that the server plugin is enabled and connected, then run /ov doctor. No extraction completion is implied.`)
      }
    } finally {
      active = false
    }
  }
  context.keymap.layer(() => ({
    commands: [{ id: "openviking.controls", title: "OpenViking controls", group: "OpenViking", palette: true,
      slash: { name: "ov", arguments: true }, run: async (input) => {
        if (disposed || active) return
        const action = input?.trim()
        if (action) return run(action)
        dialogID++
        dialogOpen = false
        dialogOwned = true
        const selected = await context.ui.dialog.select({ title: "OpenViking", options: ACTIONS.map((value) => ({
          title: value === "commit" ? "Commit current session…" : value[0].toUpperCase() + value.slice(1), value,
        })) })
        dialogOwned = false
        if (selected) await run(selected)
      } }],
  }))
  const stopStatus = context.ui.slot({ append: "prompt.footer.status", render: renderStatus })
  return () => {
    disposed = true
    generation++
    controller.abort()
    stopStatus()
    if (dialogOwned) context.ui.dialog.clear()
    // OpenCode owns the keymap layer and removes it on reload/disable.
  }
}
