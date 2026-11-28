#!/usr/bin/env node
// Prints the host table of docs/<lang>/agent-integrations/16-capability-reference.md
// from the host modules themselves: `node host-table.mjs --lang en|zh`.

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const EXAMPLES = join(dirname(fileURLToPath(import.meta.url)), "..");
const HOOK_HOSTS = ["claude-code", "codex", "cursor", "trae", "antigravity", "zcode", "kimicode", "codebuddy", "qoder"];
const MODULES = [
  ...HOOK_HOSTS.map((id) => join("agent-hook-plugin", "hosts", id, "host.mjs")),
  join("opencode-plugin", "host.mjs"),
  join("dsh-memory-plugin", "host.mjs"),
  join("pi-coding-agent-extension", "host.mjs"),
];
const STAGES = ["start", "prompt", "turnEnd", "compactSoon", "end", "toolCall"];

const WORDS = {
  en: {
    header: ["Host", "Session start", "Prompt", "Turn end", "Before compaction", "Session end", "Tool call", "Has session end", "Profile injected at", "Writes may detach"],
    yes: "yes",
    no: "no",
    after: "after compaction",
    start: "session start",
    firstPrompt: "first prompt",
  },
  zh: {
    header: ["宿主", "会话开始", "提交 prompt", "轮次结束", "压缩前", "会话结束", "工具调用", "有会话结束事件", "画像注入时机", "写入可脱离进程"],
    yes: "是",
    no: "否",
    after: "压缩后",
    start: "会话开始",
    firstPrompt: "首个 prompt",
  },
};

export async function loadHosts() {
  const hosts = [];
  for (const path of MODULES) {
    const module = await import(pathToFileURL(join(EXAMPLES, path)).href);
    hosts.push(...Object.values(module.hosts || { [module.default.id]: module.default }));
  }
  return hosts;
}

const code = (names) => names.map((name) => `\`${name}\``).join(", ");

function row(host, w) {
  const events = (stage) => Object.keys(host.events).filter((event) => host.events[event] === stage);
  const cells = STAGES.map((stage) => {
    let text = code(events(stage));
    // A host that reports a compaction only after it happened.
    if (stage === "compactSoon" && !text && events("compacted").length) text = `${code(events("compacted"))} (${w.after})`;
    return text || "—";
  });
  const detaches = Boolean(host.canDetach || host.detachEvents?.length);
  return [
    host.id,
    ...cells,
    events("end").length ? w.yes : w.no,
    host.facts?.profileAt === "firstPrompt" ? w.firstPrompt : w.start,
    detaches ? w.yes : w.no,
  ];
}

export async function hostTable(lang = "en") {
  const w = WORDS[lang];
  if (!w) throw new Error(`unknown language: ${lang}`);
  const lines = [w.header, w.header.map(() => "---"), ...(await loadHosts()).map((host) => row(host, w))];
  return lines.map((cells) => `| ${cells.join(" | ")} |`).join("\n");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const at = process.argv.indexOf("--lang");
  console.log(await hostTable(at > 0 ? process.argv[at + 1] : "en"));
}
