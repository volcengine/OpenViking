// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// JSON.parse discards repeated keys, including escaped spellings of the same
// alias. Reject them at both tool transports before that information is lost.
export function parseUniqueJson(text) {
  const value = JSON.parse(text);
  const stack = [];
  const tokens = /"(?:\\[\s\S]|[^"\\])*"|[{}\[\]]/g;
  for (const match of text.matchAll(tokens)) {
    const token = match[0];
    if (token === '{' || token === '[') stack.push(token === '{' ? new Set() : null);
    else if (token === '}' || token === ']') stack.pop();
    else {
      let next = match.index + token.length;
      while (' \t\r\n'.includes(text[next] ?? '\0')) next++;
      if (text[next] !== ':') continue;
      const key = JSON.parse(token);
      const keys = stack.at(-1);
      if (keys.has(key)) throw new Error(`duplicate JSON key: ${key}`);
      keys.add(key);
    }
  }
  return value;
}
