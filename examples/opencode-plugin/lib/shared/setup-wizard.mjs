// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Interactive ovcli.conf setup wizard shared by the OpenViking memory plugins.
 *
 * Lets a pure-marketplace install (no installer script) configure the server
 * URL and API key that both the lifecycle hooks and the stdio MCP proxy read:
 *
 *   node <plugin>/scripts/setup.mjs
 *
 * Conventions: show current values, keep existing secrets on empty input,
 * merge-write (never drop unknown fields), back up the previous file, 0600.
 */

import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync, chmodSync } from "node:fs";
import { dirname } from "node:path";
import { createInterface } from "node:readline/promises";
import { loadCredentialFiles } from "./credentials.mjs";

const CLOUD_URL = "https://api.vikingdb.cn-beijing.volces.com/openviking";
const LOCAL_URL = "http://127.0.0.1:1933";

function maskSecret(value) {
  const s = String(value || "");
  if (!s) return "(not set)";
  if (s.length <= 8) return "****";
  return `${s.slice(0, 4)}…${s.slice(-4)} (${s.length} chars)`;
}

function readJsonSafe(path) {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return {};
  }
}

export async function runSetupWizard({
  input = process.stdin,
  output = process.stdout,
  env = process.env,
} = {}) {
  const { cliPath } = loadCredentialFiles(env);
  const current = readJsonSafe(cliPath);
  const rl = createInterface({ input, output });
  const say = (line = "") => output.write(`${line}\n`);

  try {
    say("OpenViking memory plugin setup");
    say(`Config file: ${cliPath}`);
    say("");
    say("Current values:");
    say(`  url:     ${current.url || "(not set)"}`);
    say(`  api_key: ${maskSecret(current.api_key)}`);
    if (current.account) say(`  account: ${current.account}`);
    if (current.user) say(`  user:    ${current.user}`);
    say("");

    const defaultUrl = current.url || LOCAL_URL;
    say("Where do you connect to OpenViking?");
    say(`  1) Self-hosted / local        [${LOCAL_URL}]`);
    say(`  2) Volcengine OpenViking Cloud [${CLOUD_URL}]`);
    say(`  3) Custom URL / keep current   [${defaultUrl}]`);
    const mode = (await rl.question("Choice [1/2/3, default 3]: ")).trim();
    let url = defaultUrl;
    if (mode === "1") {
      url = LOCAL_URL;
    } else if (mode === "2") {
      url = CLOUD_URL;
    } else {
      const answer = (await rl.question(`Server URL [${defaultUrl}]: `)).trim();
      if (answer) url = answer;
    }

    const keyPrompt = current.api_key
      ? `API key [enter = keep ${maskSecret(current.api_key)}, '-' = clear]: `
      : "API key (leave empty for unauthenticated local mode): ";
    const keyAnswer = await rl.question(keyPrompt);
    let apiKey = current.api_key || "";
    if (keyAnswer === "-") apiKey = "";
    else if (keyAnswer.trim()) apiKey = keyAnswer.trim();
    else if (!current.api_key) apiKey = "";

    const next = { ...current, url, api_key: apiKey };

    say("");
    say("Changes:");
    say(`  url:     ${current.url || "(not set)"} -> ${next.url}`);
    say(`  api_key: ${maskSecret(current.api_key)} -> ${maskSecret(next.api_key)}`);
    const confirm = (await rl.question("Write config? [Y/n] ")).trim().toLowerCase();
    if (confirm === "n" || confirm === "no") {
      say("Aborted; nothing written.");
      return { written: false, path: cliPath };
    }

    mkdirSync(dirname(cliPath), { recursive: true });
    if (existsSync(cliPath)) {
      copyFileSync(cliPath, `${cliPath}.bak.${Date.now()}`);
    }
    writeFileSync(cliPath, `${JSON.stringify(next, null, 2)}\n`, { mode: 0o600 });
    try {
      chmodSync(cliPath, 0o600);
    } catch {
      /* best effort on platforms without chmod semantics */
    }
    say(`Written: ${cliPath}`);
    say("The stdio MCP proxy and hooks pick this up on the next harness start.");
    return { written: true, path: cliPath };
  } finally {
    rl.close();
  }
}
