import type { FilePreviewSource } from './types.js';

export interface SessionPreviewMessage {
  id: string;
  role: string;
  content: string;
  createdAt?: string;
}

const string = (value: unknown) =>
  typeof value === 'string' ? value : undefined;
const record = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

/** Only session transcripts receive the conversation layout, not arbitrary JSONL. */
export function isSessionMessagesSource(source: FilePreviewSource): boolean {
  if (source.fileName !== 'messages.jsonl' || source.truncated) return false;
  const path = source.path;
  if (!path || !/^viking:\/\//i.test(path)) return false;
  const segments = path.slice('viking://'.length).split('/');
  if (segments.at(-1) !== 'messages.jsonl') return false;
  const start =
    segments[0] === '~' && segments[1] === 'sessions'
      ? 2
      : segments[0] === 'user' && segments[1] && segments[2] === 'sessions'
        ? 3
        : undefined;
  return (
    start !== undefined &&
    segments.length > start + 1 &&
    segments
      .slice(1, -1)
      .every((segment) => segment && segment !== '.' && segment !== '..')
  );
}

function partText(part: unknown): string | undefined {
  if (!record(part)) return undefined;
  if (part.type === 'text') return string(part.text);
  if (part.type === 'image_url' || part.type === 'context')
    return referencePartText(part);
  if (part.type !== 'tool') return undefined;
  for (const key of ['tool_name', 'toolName', 'tool_status', 'toolStatus'])
    if (part[key] !== undefined && typeof part[key] !== 'string')
      return undefined;
  const name = string(part.tool_name) ?? string(part.toolName) ?? 'tool';
  const status = string(part.tool_status) ?? string(part.toolStatus);
  const output =
    part.tool_output === undefined ? part.toolOutput : part.tool_output;
  return [
    `${name}${status ? ` · ${status}` : ''}`,
    output === undefined
      ? undefined
      : typeof output === 'string'
        ? output
        : JSON.stringify(output, null, 2),
  ]
    .filter((value) => value !== undefined)
    .join('\n');
}

/** Reference metadata stays readable without loading images or opening resources. */
function referencePartText(part: Record<string, unknown>): string | undefined {
  if (part.type === 'image_url') {
    const image = part.image_url;
    if (typeof image === 'string') {
      if (!image.trim()) return;
    } else if (
      !record(image) ||
      !string(image.url)?.trim() ||
      (image.detail !== undefined && typeof image.detail !== 'string')
    )
      return;
  } else {
    if (
      ['uri', 'context_type', 'abstract'].some(
        (key) => part[key] !== undefined && typeof part[key] !== 'string',
      ) ||
      (part.context_type !== undefined &&
        !['memory', 'resource', 'skill'].includes(
          string(part.context_type) ?? '',
        ))
    )
      return;
  }
  return Object.entries(part)
    .map(([key, value]) => {
      const text =
        typeof value === 'string' ? value : JSON.stringify(value, null, 2);
      return `${key}: ${text ?? ''}`;
    })
    .join('\n');
}

function messageContent(message: Record<string, unknown>): string | undefined {
  const legacy = string(message.content);
  if (message.content !== undefined && legacy === undefined) return undefined;
  const parts = message.parts;
  if (parts !== undefined && !Array.isArray(parts)) return undefined;
  const texts: string[] = [];
  for (const part of parts ?? []) {
    const text = partText(part);
    if (text === undefined) return undefined;
    texts.push(text);
  }
  const body = texts.join('\n\n');
  if (legacy && body && legacy !== body) return undefined;
  return body || legacy;
}

/** Reject the entire special preview on malformed/unsupported records, retaining JSONL. */
export function parseSessionMessages(
  content: string,
): readonly SessionPreviewMessage[] | undefined {
  const messages: SessionPreviewMessage[] = [];
  for (const line of content.split(/\r?\n/).filter((line) => line.trim())) {
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      return undefined;
    }
    if (!record(value)) return undefined;
    const role = string(value.role);
    const body = messageContent(value);
    if (!role?.trim() || !body?.trim()) return undefined;
    messages.push({
      id: string(value.id) ?? `message-${messages.length}`,
      role,
      content: body,
      createdAt: string(value.created_at) ?? string(value.createdAt),
    });
  }
  return messages.length ? messages : undefined;
}
