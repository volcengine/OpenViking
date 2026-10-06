export async function readHookInput() {
  let raw = "";
  for await (const chunk of process.stdin) raw += chunk;
  if (!raw.trim()) return {};
  return JSON.parse(raw);
}

export function output(value = {}) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

export async function runHook(fn) {
  try {
    const input = await readHookInput();
    output((await fn(input)) || {});
  } catch {
    // Observability must never block or alter the Codex turn.
    output({});
  }
}
