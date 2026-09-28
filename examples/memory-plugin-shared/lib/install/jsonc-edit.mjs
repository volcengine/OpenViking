#!/usr/bin/env node
/**
 * Comment-preserving edits to OpenCode's config file.
 *
 * OpenCode reads `opencode.jsonc`, and people keep notes in it. Reparsing and
 * reserializing the file would silently eat every comment and every hand-made
 * formatting choice, so the value is parsed only to decide what to change and
 * the change itself is spliced into the original text. That means walking JSONC
 * by hand: comments, trailing commas and single-quoted strings are all things
 * `JSON.parse` refuses and the splice has to step over.
 *
 * Installer-only, and deliberately outside the runtime closure `sync.mjs`
 * vendors into the plugins: no hook imports it, so no plugin ships it.
 */

import fs from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

function stripJsonc(s) {
  let out = "";
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    const next = s[i + 1];
    if (ch === '"' || ch === "'") {
      const end = readStringEnd(s, i);
      out += s.slice(i, end);
      i = end;
    } else if (ch === "/" && next === "/") {
      i += 2;
      while (i < s.length && s[i] !== "\n") i++;
    } else if (ch === "/" && next === "*") {
      i += 2;
      while (i < s.length && !(s[i] === "*" && s[i + 1] === "/")) i++;
      i = Math.min(s.length, i + 2);
    } else {
      out += ch;
      i++;
    }
  }
  return out.replace(/,\s*([}\]])/g, "$1");
}

function readStringEnd(s, start) {
  const quote = s[start];
  let i = start + 1;
  while (i < s.length) {
    if (s[i] === "\\") {
      i += 2;
    } else if (s[i] === quote) {
      return i + 1;
    } else {
      i++;
    }
  }
  return s.length;
}

function skipTrivia(s, i, end = s.length) {
  while (i < end) {
    if (/\s/.test(s[i])) {
      i++;
    } else if (s[i] === "/" && s[i + 1] === "/") {
      i += 2;
      while (i < end && s[i] !== "\n") i++;
    } else if (s[i] === "/" && s[i + 1] === "*") {
      i += 2;
      while (i < end && !(s[i] === "*" && s[i + 1] === "/")) i++;
      i = Math.min(end, i + 2);
    } else {
      break;
    }
  }
  return i;
}

function parseStringLiteral(s, start) {
  const end = readStringEnd(s, start);
  try {
    return { value: JSON.parse(s.slice(start, end)), end };
  } catch {
    return { value: "", end };
  }
}

function findTopLevelObject(s) {
  const start = skipTrivia(s, 0);
  if (s[start] !== "{") return null;
  let depth = 0;
  let i = start;
  while (i < s.length) {
    if (s[i] === '"' || s[i] === "'") {
      i = readStringEnd(s, i);
      continue;
    }
    if (s[i] === "/" && (s[i + 1] === "/" || s[i + 1] === "*")) {
      i = skipTrivia(s, i);
      continue;
    }
    if (s[i] === "{" || s[i] === "[") depth++;
    if (s[i] === "}" || s[i] === "]") {
      depth--;
      if (depth === 0 && s[i] === "}") return { start, end: i };
    }
    i++;
  }
  return null;
}

function findObjectRangeAt(s, start, end = s.length) {
  const objectStart = skipTrivia(s, start, end);
  if (s[objectStart] !== "{") return null;
  let depth = 0;
  let i = objectStart;
  while (i < end) {
    if (s[i] === '"' || s[i] === "'") {
      i = readStringEnd(s, i);
      continue;
    }
    if (s[i] === "/" && (s[i + 1] === "/" || s[i + 1] === "*")) {
      i = skipTrivia(s, i, end);
      continue;
    }
    if (s[i] === "{" || s[i] === "[") depth++;
    if (s[i] === "}" || s[i] === "]") {
      depth--;
      if (depth === 0 && s[i] === "}") return { start: objectStart, end: i };
    }
    i++;
  }
  return null;
}

function findArrayRangeAt(s, start, end = s.length) {
  const arrayStart = skipTrivia(s, start, end);
  if (s[arrayStart] !== "[") return null;
  let depth = 0;
  let i = arrayStart;
  while (i < end) {
    if (s[i] === '"' || s[i] === "'") {
      i = readStringEnd(s, i);
      continue;
    }
    if (s[i] === "/" && (s[i + 1] === "/" || s[i + 1] === "*")) {
      i = skipTrivia(s, i, end);
      continue;
    }
    if (s[i] === "{" || s[i] === "[") depth++;
    if (s[i] === "}" || s[i] === "]") {
      depth--;
      if (depth === 0 && s[i] === "]") return { start: arrayStart, end: i };
    }
    i++;
  }
  return null;
}

function findTopLevelProperty(s, objectRange, name) {
  let depth = 1;
  let i = objectRange.start + 1;
  while (i < objectRange.end) {
    if (s[i] === "/" && (s[i + 1] === "/" || s[i + 1] === "*")) {
      i = skipTrivia(s, i, objectRange.end);
      continue;
    }
    if (s[i] === '"' || s[i] === "'") {
      const keyStart = i;
      const parsed = parseStringLiteral(s, i);
      i = parsed.end;
      const afterKey = skipTrivia(s, i, objectRange.end);
      if (depth === 1 && parsed.value === name && s[afterKey] === ":") {
        const valueStart = skipTrivia(s, afterKey + 1, objectRange.end);
        return {
          keyStart,
          valueStart,
          replaceEnd: findPropertyReplaceEnd(s, valueStart, objectRange.end),
        };
      }
      continue;
    }
    if (s[i] === "{" || s[i] === "[") depth++;
    if (s[i] === "}" || s[i] === "]") depth--;
    i++;
  }
  return null;
}

function findPropertyReplaceEnd(s, valueStart, objectEnd) {
  let depth = 0;
  let i = skipTrivia(s, valueStart, objectEnd);
  let lastTokenEnd = i;
  while (i < objectEnd) {
    if (s[i] === '"' || s[i] === "'") {
      i = readStringEnd(s, i);
      lastTokenEnd = i;
      continue;
    }
    if (s[i] === "/" && (s[i + 1] === "/" || s[i + 1] === "*")) {
      i = skipTrivia(s, i, objectEnd);
      continue;
    }
    if (depth === 0 && s[i] === ",") return lastTokenEnd;
    if (s[i] === "{" || s[i] === "[") depth++;
    if (s[i] === "}" || s[i] === "]") depth--;
    if (!/\s/.test(s[i])) lastTokenEnd = i + 1;
    i++;
  }
  return lastTokenEnd;
}

function findLineIndent(s, index) {
  const lineStart = s.lastIndexOf("\n", index - 1) + 1;
  const prefix = s.slice(lineStart, index);
  return /^[ \t]*$/.test(prefix) ? prefix : "";
}

function detectPropertyIndent(s, objectRange) {
  let i = objectRange.start + 1;
  while (i < objectRange.end) {
    i = skipTrivia(s, i, objectRange.end);
    if (s[i] === '"' || s[i] === "'") return findLineIndent(s, i) || "  ";
    if (s[i] === "{" || s[i] === "[") break;
    i++;
  }
  const closeIndent = findLineIndent(s, objectRange.end);
  return `${closeIndent}  `;
}

function hasTopLevelProperty(s, objectRange) {
  let i = objectRange.start + 1;
  while (i < objectRange.end) {
    i = skipTrivia(s, i, objectRange.end);
    if (s[i] === '"' || s[i] === "'") return true;
    i++;
  }
  return false;
}

function objectEndsWithComma(s, objectRange) {
  const body = s.slice(objectRange.start + 1, objectRange.end);
  return body.trimEnd().endsWith(",");
}

function rangeHasValue(s, range) {
  let i = range.start + 1;
  while (i < range.end) {
    i = skipTrivia(s, i, range.end);
    if (i < range.end) return true;
  }
  return false;
}

function formatProperty(name, value, indent) {
  const json = JSON.stringify(value, null, 2);
  const formatted = json.split("\n").map((line, idx) => idx === 0 ? line : `${indent}${line}`).join("\n");
  return `${JSON.stringify(name)}: ${formatted}`;
}

function setPropertyInObject(s, objectRange, name, value) {
  const existing = findTopLevelProperty(s, objectRange, name);
  if (existing) {
    const indent = findLineIndent(s, existing.keyStart) || detectPropertyIndent(s, objectRange);
    return `${s.slice(0, existing.keyStart)}${formatProperty(name, value, indent)}${s.slice(existing.replaceEnd)}`;
  }

  const indent = detectPropertyIndent(s, objectRange);
  const closeIndent = findLineIndent(s, objectRange.end);
  const needsComma = hasTopLevelProperty(s, objectRange) && !objectEndsWithComma(s, objectRange);
  const prefix = needsComma ? "," : "";
  const insertion = `${prefix}\n${indent}${formatProperty(name, value, indent)}\n${closeIndent}`;
  return `${s.slice(0, objectRange.end)}${insertion}${s.slice(objectRange.end)}`;
}

function setTopLevelProperty(s, name, value) {
  let objectRange = findTopLevelObject(s);
  if (!objectRange) {
    s = "{\n}\n";
    objectRange = findTopLevelObject(s);
  }
  return setPropertyInObject(s, objectRange, name, value);
}

function setNestedObjectProperty(s, parentName, childName, childValue, fallbackParentValue) {
  let objectRange = findTopLevelObject(s);
  if (!objectRange) {
    s = "{\n}\n";
    objectRange = findTopLevelObject(s);
  }
  const parent = findTopLevelProperty(s, objectRange, parentName);
  if (!parent) return setPropertyInObject(s, objectRange, parentName, fallbackParentValue);
  const parentRange = findObjectRangeAt(s, parent.valueStart, parent.replaceEnd);
  if (!parentRange) return setPropertyInObject(s, objectRange, parentName, fallbackParentValue);
  return setPropertyInObject(s, parentRange, childName, childValue);
}

function appendStringToTopLevelArray(s, name, value) {
  let objectRange = findTopLevelObject(s);
  if (!objectRange) {
    s = "{\n}\n";
    objectRange = findTopLevelObject(s);
  }
  const prop = findTopLevelProperty(s, objectRange, name);
  if (!prop) return setPropertyInObject(s, objectRange, name, [value]);
  const arrayRange = findArrayRangeAt(s, prop.valueStart, prop.replaceEnd);
  if (!arrayRange) return setPropertyInObject(s, objectRange, name, [value]);
  const propIndent = findLineIndent(s, prop.keyStart) || detectPropertyIndent(s, objectRange);
  const itemIndent = `${propIndent}  `;
  const closeIndent = findLineIndent(s, arrayRange.end) || propIndent;
  const needsComma = rangeHasValue(s, arrayRange) && !s.slice(arrayRange.start + 1, arrayRange.end).trimEnd().endsWith(",");
  const prefix = needsComma ? "," : "";
  const insertion = `${prefix}\n${itemIndent}${JSON.stringify(value)}\n${closeIndent}`;
  return `${s.slice(0, arrayRange.end)}${insertion}${s.slice(arrayRange.end)}`;
}

/** The config text with the plugin registered and the MCP fallback pointed at `mcpProxy`. */
export function updateOpencodeConfig(raw, { pluginSpec = "", mcpProxy = "" } = {}) {
  let data = {};
  try { data = raw.trim() ? JSON.parse(stripJsonc(raw)) : {}; } catch { data = {}; }
  let nextRaw = raw.trim() ? raw : "{\n}\n";
  if (pluginSpec) {
    const next = Array.isArray(data.plugin) ? data.plugin.slice() : [];
    if (!next.includes(pluginSpec)) {
      next.push(pluginSpec);
      nextRaw = appendStringToTopLevelArray(nextRaw, "plugin", pluginSpec);
    }
    data.plugin = next;
  }
  if (mcpProxy) {
    data.mcp = data.mcp && typeof data.mcp === "object" && !Array.isArray(data.mcp) ? data.mcp : {};
    if (!data.mcp.openviking || data.mcp.openviking.enabled !== false) {
      data.mcp.openviking = {
        type: "local",
        command: ["node", mcpProxy],
        enabled: true,
        timeout: 15000,
      };
      nextRaw = setNestedObjectProperty(nextRaw, "mcp", "openviking", data.mcp.openviking, data.mcp);
    }
  }
  if (!nextRaw.endsWith("\n")) nextRaw += "\n";
  return nextRaw;
}

export { stripJsonc };

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const file = process.argv[2];
  let raw = "";
  try { raw = fs.readFileSync(file, "utf8"); } catch {}
  fs.writeFileSync(file, updateOpencodeConfig(raw, {
    pluginSpec: process.argv[3] || "",
    mcpProxy: process.argv[4] || "",
  }));
}
