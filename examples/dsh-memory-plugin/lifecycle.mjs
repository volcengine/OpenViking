export async function injectStartupProfile(agent, runtime) {
  await runtime.initialize(agent);
  if (agent.status !== "idle") return false;
  const profile = await runtime.profileMessage(agent);
  if (!profile) return false;
  agent.inject(profile);
  return true;
}
