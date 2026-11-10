// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The model writes ordinary Markdown; Slack shows its own mrkdwn. Everything
// is escaped first, so model text can never become a Slack control token
// such as <!channel>: the only markup that goes out is what is built here.

const escape = (text) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function prose(text) {
  return escape(text)
    .replace(/\[([^\]\n]+)\]\(((?:https?:\/\/|mailto:)[^\s)|]+)\)/g, '<$2|$1>')
    .replace(/^(\s*)[-*+]\s+/gm, '$1• ')
    .replace(/(^|[^*])\*(?!\*)([^*\n]+?)\*(?!\*)/g, '$1_$2_')
    .replace(/\*\*([^*\n]+?)\*\*/g, '*$1*')
    .replace(/__([^_\n]+?)__/g, '*$1*')
    .replace(/~~([^~\n]+?)~~/g, '~$1~')
    .replace(/^(\s*)#{1,6}\s+(.+?)\s*#*\s*$/gm, '$1*$2*');
}

function inline(text) {
  return text.split(/(`[^`\n]+`)/g).map((part, index) => (index % 2 ? escape(part) : prose(part))).join('');
}

// A resolved name written inline as "@Name" becomes that person's mention in
// place; every other mention is prefixed, as on Lark.
export function toMrkdwn(markdown, mentions = []) {
  let text = String(markdown).trim().split(/(```[\s\S]*?```)/g)
    .map((part, index) => (index % 2 ? escape(part) : inline(part))).join('');
  const inlined = new Set();
  for (const { id, name } of [...mentions].filter((item) => item.name).sort((a, b) => b.name.length - a.name.length)) {
    for (const spelled of new Set([name, name.replaceAll('_', ' ')].map(escape))) {
      if (!text.includes(`@${spelled}`)) continue;
      text = text.split(`@${spelled}`).join(`<@${id}>`);
      inlined.add(id);
    }
  }
  const prefix = [...new Set(mentions.map(({ id }) => id))].filter((id) => !inlined.has(id)).map((id) => `<@${id}>`).join(' ');
  return prefix ? `${prefix} ${text}` : text;
}
