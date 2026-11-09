export async function injectStartupProfile(agent, runtime, signal) {
  await runtime.initialize(agent);
  if (signal?.aborted || agent.status !== "idle") return false;
  const profile = await runtime.profileMessage(agent);
  if (!profile) return false;
  agent.inject(profile);
  return true;
}
