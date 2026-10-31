import { recallRequest } from "./runtime.mjs";

export async function onRequest(body, ctx) {
  return recallRequest(body, ctx);
}
