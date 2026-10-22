import i18n from '#/i18n/core'
import { isConfigFileObject } from '#/routes/settings/-lib/config-file-api'

/** A gateway management key in the format `openviking-server init` generates. */
export function generateManagementKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32))
  const base64 = btoa(String.fromCharCode(...bytes))
  return `ovgm_${base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')}`
}

type Member = {
  key: string
  keyStart: number
  valueStart: number
  valueEnd: number
}
type Edit = { at: number; end: number; text: string }

function skipWhitespace(text: string, index: number): number {
  while (index < text.length && /\s/.test(text[index])) index += 1
  return index
}

function skipString(text: string, index: number): number {
  index += 1
  while (index < text.length && text[index] !== '"')
    index += text[index] === '\\' ? 2 : 1
  return index + 1
}

/**
 * End of the value that starts at `index`. Unquoted `$VAR` and `${VAR}`
 * references count as values: the server expands them before parsing.
 */
function skipValue(text: string, index: number): number {
  const first = text[index]
  if (first === '"') return skipString(text, index)
  if (first === '{' || first === '[') {
    let depth = 0
    while (index < text.length) {
      const char = text[index]
      if (char === '"') {
        index = skipString(text, index)
        continue
      }
      if (char === '{' || char === '[') depth += 1
      if (char === '}' || char === ']') depth -= 1
      index += 1
      if (depth === 0) break
    }
    return index
  }
  if (text.startsWith('${', index)) return text.indexOf('}', index) + 1
  while (index < text.length && !/[\s,}\]]/.test(text[index])) index += 1
  return index
}

/** Members of the object that opens at `start`, with their positions. */
function objectMembers(text: string, start: number): Member[] {
  const members: Member[] = []
  let index = skipWhitespace(text, start + 1)
  while (text[index] === '"') {
    const keyStart = index
    index = skipString(text, index)
    const key = JSON.parse(text.slice(keyStart, index)) as string
    // Skip the colon and the whitespace around it.
    const valueStart = skipWhitespace(text, skipWhitespace(text, index) + 1)
    const valueEnd = skipValue(text, valueStart)
    members.push({ key, keyStart, valueStart, valueEnd })
    index = skipWhitespace(text, valueEnd)
    if (text[index] === ',') index = skipWhitespace(text, index + 1)
  }
  return members
}

/** JSON parsers keep the last of duplicate keys, so that one counts. */
function lastMember(members: Member[], key: string): Member | undefined {
  return members.filter((member) => member.key === key).at(-1)
}

/** Indentation of the line that `index` is on. */
function lineIndent(text: string, index: number): string {
  const lineStart = text.lastIndexOf('\n', index - 1) + 1
  return /^[ \t]*/.exec(text.slice(lineStart, index))?.[0] ?? ''
}

/** A new object literal laid out like a member indented by `indent`. */
function objectText(fields: Array<[string, string]>, indent: string): string {
  const inner = indent + (indent || '  ')
  return `{\n${fields
    .map(([key, value]) => `${inner}${JSON.stringify(key)}: ${value}`)
    .join(',\n')}\n${indent}}`
}

/**
 * Edits that set `fields` (JSON-encoded values) in the object at `start`.
 * Existing members change in place; new members follow the last one with
 * the separator the object already uses.
 */
function setFields(
  text: string,
  start: number,
  fields: Array<[string, string]>,
): Edit[] {
  const members = objectMembers(text, start)
  const edits: Edit[] = []
  const missing: Array<[string, string]> = []
  for (const [key, value] of fields) {
    const member = lastMember(members, key)
    if (member)
      edits.push({ at: member.valueStart, end: member.valueEnd, text: value })
    else missing.push([key, value])
  }
  if (!missing.length) return edits
  const last = members.at(-1)
  if (!last) {
    const indent = lineIndent(text, start)
    edits.push({
      at: start,
      end: skipValue(text, start),
      text: objectText(missing, indent),
    })
    return edits
  }
  // The whitespace between `{` and the first member, e.g. "\n  ".
  const separator = text.slice(start + 1, members[0].keyStart) || ' '
  edits.push({
    at: last.valueEnd,
    end: last.valueEnd,
    text: missing
      .map(([key, value]) => `,${separator}${JSON.stringify(key)}: ${value}`)
      .join(''),
  })
  return edits
}

/**
 * Turns the gateway on in the text of ov.conf, optionally with a gateway
 * management key. Only the `gateway` section changes: every other member,
 * the existing gateway fields, formatting and `$VAR` references stay as they are.
 */
export function enableGatewayContent(
  content: string,
  managementKey?: string,
): string {
  if (!isConfigFileObject(content))
    throw new Error(i18n.t('studio:errors.invalidConfigObject'))
  const fields: Array<[string, string]> = [['enabled', 'true']]
  if (managementKey)
    fields.push(['management_key', JSON.stringify(managementKey)])

  // A leading byte order mark stays in place; the scanner starts after it.
  const root = skipWhitespace(content, content.startsWith('\uFEFF') ? 1 : 0)
  const gateway = lastMember(objectMembers(content, root), 'gateway')
  let edits: Edit[]
  if (gateway && content[gateway.valueStart] === '{') {
    edits = setFields(content, gateway.valueStart, fields)
  } else if (gateway) {
    edits = [
      {
        at: gateway.valueStart,
        end: gateway.valueEnd,
        text: objectText(fields, lineIndent(content, gateway.keyStart)),
      },
    ]
  } else {
    const first = objectMembers(content, root).at(0)
    const section = !first
      ? objectText(fields, '  ')
      : content.slice(root + 1, first.keyStart).includes('\n')
        ? objectText(fields, lineIndent(content, first.keyStart))
        : // A single-line file gets a single-line section.
          `{${fields.map(([key, value]) => `${JSON.stringify(key)}: ${value}`).join(', ')}}`
    edits = setFields(content, root, [['gateway', section]])
  }
  let result = content
  for (const edit of edits.sort((left, right) => right.at - left.at))
    result = result.slice(0, edit.at) + edit.text + result.slice(edit.end)
  if (!isConfigFileObject(result))
    throw new Error(i18n.t('studio:errors.configUpdateFailed'))
  return result
}

/** Whether a save or restart was refused because ov.conf changed meanwhile. */
export function isRevisionConflict(error: unknown): boolean {
  return error instanceof Error && /ov\.conf changed/i.test(error.message)
}
