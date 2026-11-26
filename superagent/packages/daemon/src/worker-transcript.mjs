// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The last messages of a worker session, read from the file its runtime keeps
// (worker-executor.mjs finds it), for the worker that takes the task over on
// another runtime (tasks.mjs, handover). One runtime cannot resume another's
// session: the new worker starts a session of its own from the task record,
// these messages, and the path of the whole file to read more when it needs to.
//
// Only what was said is shown: the inputs the worker got (each by its first
// line: they are the daemon's briefs, which the task record repeats), its own
// words, and the tool calls it made (one line per call). Tool output and
// reasoning are left out: they are long, and the directory holds the real state.

import fs from 'node:fs';

// What the handover shows, at most.
export const HANDOVER_ENTRIES = 8;
export const HANDOVER_ENTRY_CHARS = 1_200;
export const HANDOVER_TOOL_CHARS = 160;
export const HANDOVER_INPUT_CHARS = 200;
export const HANDOVER_CHARS = 6_000;
// Only the end of a session is read: it is where the last messages are.
const TAIL_BYTES = 2 * 1024 * 1024;

export const TRANSCRIPT_FORMATS = Object.freeze({
  pi: 'Pi session JSONL: one event per line; `"type":"message"` lines hold the conversation (message.role user, assistant, toolResult)',
  codex: 'Codex rollout JSONL: one event per line; `"type":"response_item"` lines hold the messages, tool calls and their output',
  traex: 'TraeX rollout JSONL (Codex\'s format): `"type":"response_item"` lines hold the messages, tool calls and their output',
  claude: 'Claude Code session JSONL: one event per line; `"type":"user"` and `"type":"assistant"` lines hold the conversation',
});

function oneLine(text) {
  return String(text ?? '').replace(/\s+/g, ' ').trim();
}

function clip(text, chars) {
  const value = String(text ?? '').trim();
  return value.length > chars ? `${value.slice(0, chars)}…` : value;
}

function tailLines(file, bytes = TAIL_BYTES) {
  const fd = fs.openSync(file, 'r');
  try {
    const size = fs.fstatSync(fd).size;
    const start = Math.max(0, size - bytes);
    const buffer = Buffer.alloc(size - start);
    fs.readSync(fd, buffer, 0, buffer.length, start);
    const lines = buffer.toString('utf8').split('\n');
    // A read that starts mid-file starts mid-line.
    if (start > 0) lines.shift();
    return lines.filter((line) => line.trim());
  } finally {
    fs.closeSync(fd);
  }
}

const textOf = (parts, types) => (Array.isArray(parts) ? parts : [{ type: types[0], text: parts }])
  .filter((part) => types.includes(part?.type) && typeof part.text === 'string')
  .map((part) => part.text).join('\n').trim();

const call = (name, args) => oneLine(`${name} ${typeof args === 'string' ? args : JSON.stringify(args ?? {})}`);

// Each runtime's event → [{ role: 'input' | 'worker' | 'tool', text }].
const READERS = {
  pi(event) {
    if (event.type !== 'message') return [];
    const { role, content } = event.message ?? {};
    if (role === 'user') {
      const text = textOf(content, ['text']);
      return text ? [{ role: 'input', text }] : [];
    }
    if (role !== 'assistant' || !Array.isArray(content)) return [];
    return content.flatMap((part) => {
      if (part?.type === 'text' && part.text?.trim()) return [{ role: 'worker', text: part.text }];
      if (part?.type === 'toolCall') return [{ role: 'tool', text: call(part.name, part.arguments) }];
      return [];
    });
  },
  codex(event) {
    if (event.type !== 'response_item') return [];
    const item = event.payload ?? {};
    if (item.type === 'message' && item.role === 'user') {
      const text = textOf(item.content, ['input_text']);
      // Context the CLI adds by itself, not an input to the worker.
      if (!text || /^<(?:environment_context|user_instructions|permissions)/.test(text)) return [];
      return [{ role: 'input', text }];
    }
    if (item.type === 'message' && item.role === 'assistant') {
      const text = textOf(item.content, ['output_text']);
      return text ? [{ role: 'worker', text }] : [];
    }
    if (item.type === 'function_call') return [{ role: 'tool', text: call(item.name, item.arguments) }];
    if (item.type === 'custom_tool_call') return [{ role: 'tool', text: call(item.name, item.input) }];
    if (item.type === 'local_shell_call') return [{ role: 'tool', text: call('shell', item.action?.command) }];
    return [];
  },
  claude(event) {
    if ((event.type !== 'user' && event.type !== 'assistant') || event.isMeta || event.isSidechain) return [];
    const content = event.message?.content;
    if (event.type === 'user') {
      // Tool results come back as user events; they are output, not input.
      const text = typeof content === 'string' ? content.trim() : textOf(content, ['text']);
      return text ? [{ role: 'input', text }] : [];
    }
    if (typeof content === 'string') return content.trim() ? [{ role: 'worker', text: content }] : [];
    return (content ?? []).flatMap((part) => {
      if (part?.type === 'text' && part.text?.trim()) return [{ role: 'worker', text: part.text }];
      if (part?.type === 'tool_use') return [{ role: 'tool', text: call(part.name, part.input) }];
      return [];
    });
  },
};
READERS.traex = READERS.codex;

// The last entries of a session file: inputs and the worker's own words, each
// clipped, and between them its tool calls, one line each, gathered into one
// entry. Empty when the file cannot be read or its runtime is unknown.
export function recentTranscript(file, runtime, {
  entries: limit = HANDOVER_ENTRIES,
  entryChars = HANDOVER_ENTRY_CHARS,
  toolChars = HANDOVER_TOOL_CHARS,
  inputChars = HANDOVER_INPUT_CHARS,
  chars = HANDOVER_CHARS,
} = {}) {
  const reader = READERS[runtime];
  if (!file || !reader) return [];
  let lines;
  try { lines = tailLines(file); } catch { return []; }
  const entries = [];
  for (const line of lines) {
    let event;
    try { event = JSON.parse(line); } catch { continue; }
    for (const entry of reader(event)) {
      const last = entries.at(-1);
      if (entry.role === 'tool') {
        const text = clip(entry.text, toolChars);
        if (last?.role === 'tool') last.calls.push(text);
        else entries.push({ role: 'tool', calls: [text] });
      } else if (entry.role === 'input') {
        // Where a turn started: its brief's first line.
        const [first, ...rest] = String(entry.text).trim().split('\n');
        entries.push({ role: 'input', text: `${clip(first, inputChars)}${rest.some((line) => line.trim()) ? ' (…)' : ''}` });
      } else {
        entries.push({ role: entry.role, text: clip(entry.text, entryChars) });
      }
    }
  }
  const shown = [];
  let total = 0;
  for (const entry of entries.slice(-limit).reverse()) {
    // Tool calls: the last few of a run of calls say what it was doing.
    const text = entry.role === 'tool'
      ? [...(entry.calls.length > 4 ? [`(${entry.calls.length - 4} earlier calls)`] : []), ...entry.calls.slice(-4)].join('\n')
      : entry.text;
    if (total + text.length > chars && shown.length > 0) break;
    total += text.length;
    shown.unshift({ role: entry.role, text });
  }
  return shown;
}
