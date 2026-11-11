import fs from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";
import { readJSON, AdapterError } from "./files.mjs";
import { expand } from "./config.mjs";

// Magpie documents plugins.json as the owner of plugin Options. Read only this
// package's entry; do not cache a second copy or print unrelated plugin options.
export async function hostOptions(env = process.env, registryFile) {
  const configHome = isAbsolute(env.XDG_CONFIG_HOME || "") ? env.XDG_CONFIG_HOME : join(homedir(), ".config");
  const registry = await readJSON(registryFile || join(configHome, "magpie", "plugins.json"));
  if (!registry) return {};
  if (!Array.isArray(registry.plugins)) throw new AdapterError("invalid_magpie_registry");
  const root = await fs.realpath(fileURLToPath(new URL("../../", import.meta.url)));
  const local = [], packages = [];
  for (const entry of registry.plugins) {
    if (!entry || typeof entry.spec !== "string") continue;
    if (/^@openviking\/magpie-plugin(?:@[^/]+)?$/.test(entry.spec)) { packages.push(entry); continue; }
    if (!isAbsolute(entry.spec) && !entry.spec.startsWith("~/")) continue;
    try { if (await fs.realpath(expand(entry.spec)) === root) local.push(entry); }
    catch (error) { if (error.code !== "ENOENT") throw new AdapterError("invalid_magpie_registry"); }
  }
  const candidates = local.length ? local : packages;
  if (candidates.length > 1) throw new AdapterError("ambiguous_magpie_plugin_options");
  const options = candidates[0]?.options ?? {};
  if (typeof options !== "object" || Array.isArray(options)) throw new AdapterError("invalid_magpie_plugin_options");
  return options;
}
