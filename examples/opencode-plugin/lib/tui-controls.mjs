import { OPENVIKING_CONTROLS } from "./controls-contract.mjs"

const ACTIONS = { doctor: "Doctor", recall: "View saved recall", commit: "Commit current session…" }
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

export function recallText(view) {
  const lines = [
    `Session: ${view.sessionID}`,
    `Recall: ${view.state}; source: ${view.source ?? "unavailable"}`,
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
export function startTuiControls(context, { renderDialog }) {
  let controller
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
  const show = (title, text, onClose) => {
    if (disposed) return
    const id = ++dialogID
    dialogOwned = true
    dialogOpen = true
    context.ui.dialog.show(() => renderDialog(title, displayText(text)), () => {
      if (id !== dialogID) return
      dialogOpen = false
      dialogOwned = false
      onClose?.()
    })
    context.ui.dialog.set({ size: "large" })
  }
  const run = async (action) => {
    if (disposed || active) return
    active = true
    const request = ++generation
    let operation
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
          show("OpenViking", "The current session changed. Open the picker again in the intended session.")
          return
        }
      }
      operation = controller = new AbortController()
      show(`OpenViking: ${ACTIONS[action]}`, "Loading…", () => {
        // A confirmed commit may already be accepted; closing its dialog only hides the result.
        if (action === "commit") return
        operation.abort()
        if (request === generation) active = false
      })
      const rpc = context.client.rpc(OPENVIKING_CONTROLS)
      const result = await rpc[action](action === "recall" || action === "commit" ? { sessionID } : {},
        { location: location(), signal: operation.signal })
      if (disposed || request !== generation || operation.signal.aborted) return
      // A dismissed dialog must not reopen when its network request finishes.
      if (!dialogOpen || ((action === "recall" || action === "commit") && currentSession() !== sessionID)) return
      show(`OpenViking: ${ACTIONS[action]}`, action === "doctor" ? result.text
          : action === "recall" ? recallText(result)
            : COMMIT_MESSAGES[result.status] ?? "Commit returned an unsupported result. Run Doctor from the OpenViking picker.")
    } catch (error) {
      if (disposed || request !== generation || operation?.signal.aborted) return
      if (dialogOpen) {
        const kind = ["operation_failed", "rpc.not_found", "rpc.method_not_found"].includes(error?.type) ? ` (${error.type})` : ""
        show("OpenViking unavailable", `The OpenViking control could not complete${kind}. Check that the server plugin is enabled and connected, then retry from the picker.`)
      }
    } finally {
      if (controller === operation) controller = undefined
      if (request === generation) active = false
    }
  }
  context.keymap.layer(() => ({
    mode: "global",
    commands: [{ id: "openviking.controls", title: "OpenViking controls (/ov)", group: "OpenViking", palette: true,
      slash: { name: "ov" }, run: async () => {
        if (disposed || active || dialogOwned) return
        dialogID++
        dialogOpen = false
        dialogOwned = true
        const selected = await context.ui.dialog.select({ title: "OpenViking", options: Object.entries(ACTIONS).map(([value, title]) => ({
          title, value,
        })) })
        dialogOwned = false
        if (Object.hasOwn(ACTIONS, selected)) await run(selected)
      } }],
  }))
  return () => {
    disposed = true
    generation++
    controller?.abort()
    if (dialogOwned) context.ui.dialog.clear()
    // OpenCode owns the keymap layer and removes it on reload/disable.
  }
}
