// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { sanitizeMemberName } from '../roster.mjs';

// The model writes ordinary Markdown; QQ shows plain text. Only the markers
// that would show up as clutter are removed: code keeps its text, lists and
// tables stay as written, and a link keeps its address.

export const TEXT_LIMIT = 4000;

function prose(text) {
  return text
    .replace(/^(\s*)#{1,6}\s+(.+?)(?:\s+#+)?\s*$/gm, '$1$2')
    .split(/(`[^`\n]+`)/g)
    .map((part, index) => (index % 2 ? part.slice(1, -1) : part
      .replace(/!?\[([^\]\n]+)\]\(((?:https?:\/\/|mailto:)[^\s)]+)\)/g, (_, label, url) => (label === url ? url : `${label} (${url})`))
      .replace(/\*\*([^*\n]+?)\*\*/g, '$1')
      // `__init__` is a name, not emphasis: only a phrase loses its underscores.
      .replace(/(^|[^\w])__(?=\S)([^_\n]*?\s[^_\n]*?\S)__(?!\w)/gm, '$1$2')))
    .join('');
}

export function plainText(markdown) {
  return String(markdown).trim().split(/(```[^\n]*\n[\s\S]*?```)/g)
    .map((part, index) => (index % 2 ? part.replace(/^```[^\n]*\n/, '').replace(/\n?```$/, '') : prose(part)))
    .join('').trim();
}

// Text and @mentions as a OneBot segment array. A resolved name written
// inline as "@Name" becomes that person's `at` in place (the first one);
// every other mention goes in front, as on Slack. The name may be written as
// it is or as the model saw it in `from=` (sanitizeMemberName). Text
// segments are never parsed as CQ codes, so model text cannot become an @全体成员.
export function outboundSegments(text, mentions = []) {
  if (typeof text !== 'string' || !text.trim() || [...text].length > TEXT_LIMIT) {
    throw new Error(`OneBot text must contain 1–${TEXT_LIMIT} characters; split it into several messages or send it as a file`);
  }
  for (const { id } of mentions) {
    if (!/^[1-9]\d{0,15}$/.test(String(id ?? ''))) throw new Error('OneBot can only mention a person by QQ number');
  }
  const parts = [text];
  const placed = new Set();
  for (const { id, name } of [...mentions].filter((item) => item.name).sort((a, b) => b.name.length - a.name.length)) {
    if (placed.has(String(id))) continue;
    const label = sanitizeMemberName(name) ?? name;
    for (const spelled of new Set([name, name.replaceAll('_', ' '), label, label.replaceAll('_', ' ')])) {
      const index = parts.findIndex((part) => typeof part === 'string' && part.includes(`@${spelled}`));
      if (index < 0) continue;
      const at = parts[index].indexOf(`@${spelled}`);
      parts.splice(index, 1, parts[index].slice(0, at), { type: 'at', data: { qq: String(id) } },
        parts[index].slice(at + spelled.length + 1));
      placed.add(String(id));
      break;
    }
  }
  const prefix = [...new Set(mentions.map(({ id }) => String(id)))].filter((id) => !placed.has(id))
    .flatMap((id) => [{ type: 'at', data: { qq: id } }, ' ']);
  const segments = [];
  for (const part of [...prefix, ...parts]) {
    if (typeof part !== 'string') segments.push(part);
    else if (part && segments.at(-1)?.type === 'text') segments.at(-1).data.text += part;
    else if (part) segments.push({ type: 'text', data: { text: part } });
  }
  return segments;
}
