export async function injectStartupProfile(agent, runtime) {
  await runtime.initialize(agent);
  if (agent.status !== "idle") return false;
  const profile = await runtime.profileMessage(agent);
  if (!profile) return false;
  agent.inject(profile);
  return true;
}

export function registerSighupTeardown(
  dispose,
  {
    processRef = process,
    enabled = process.env.OPENVIKING_DSH_SIGHUP !== "0",
  } = {},
) {
  if (!enabled || typeof processRef?.once !== "function") return () => {};
  let handling = false;
  const handler = async () => {
    if (handling) return;
    handling = true;
    try {
      await dispose();
    } finally {
      processRef.removeListener?.("SIGHUP", handler);
      if (typeof processRef.kill === "function" && Number.isInteger(processRef.pid)) {
        processRef.kill(processRef.pid, "SIGHUP");
      }
    }
  };
  processRef.once("SIGHUP", handler);
  return () => processRef.removeListener?.("SIGHUP", handler);
}
