export async function readHookInput() {
  let raw = "";
  for await (const chunk of process.stdin) raw += chunk;
  if (!raw.trim()) return {};
  return JSON.parse(raw);
}

export function output(value = {}) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

// Import logging only on failure. A broken config, logger, or debug destination
// must not turn this optional observer into a failed memory hook.
async function logFailure(stage) {
  try {
    const { createLogger } = await import("../debug-log.mjs");
    createLogger(`usage-${stage}`).log("failure", {
      reason: "usage bookkeeping failed; memory hooks are unaffected",
    });
  } catch { /* debug reporting is best effort too */ }
}

export async function runHook(fn, stage = "hook") {
  try {
    const input = await readHookInput();
    output((await fn(input)) || {});
  } catch {
    // Never log input, source bodies, or exception text: they may contain secrets.
    await logFailure(stage);
    output({});
  }
}
