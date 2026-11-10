import { startAdapter } from "./lib/runtime.mjs";

// Magpie interprets every exported function as a plugin entry.
const started = Symbol.for("openviking.magpie-models.adapter.v1");
export default async function OpenVikingModels(input, options = {}) {
  if (!globalThis[started]) {
    globalThis[started] = startAdapter(input, options);
  }
  return {};
}
