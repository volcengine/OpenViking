import type {
  VikingBotMessage,
  VikingBotMessagePart,
} from '../../components/viking-bot/model.js';

type RawPart = Record<string, unknown>;
interface RawMessage {
  id: string;
  role: VikingBotMessage['role'];
  created_at?: string;
  turn_id?: string;
  parts?: RawPart[];
  content?: string;
}

function readPart(part: RawPart): VikingBotMessagePart[] {
  if (part.type === 'text')
    return [
      { type: 'text', text: typeof part.text === 'string' ? part.text : '' },
    ];
  if (part.type === 'reasoning')
    return [
      {
        type: 'reasoning',
        text: typeof part.reasoning === 'string' ? part.reasoning : '',
        running: false,
      },
    ];
  if (part.type === 'context')
    return [
      {
        type: 'context',
        uri: String(part.uri),
        contextType: part.context_type as 'memory' | 'resource' | 'skill',
        abstract: part.abstract as string | undefined,
      },
    ];
  if (part.type === 'image_url') {
    const image = part.image_url as { url: string; detail?: string };
    return [{ type: 'image', src: image.url, detail: image.detail }];
  }
  if (part.type === 'tool')
    return [
      {
        type: 'tool',
        id: String(part.tool_id),
        name: String(part.tool_name),
        status: (part.tool_status ?? 'completed') as 'completed',
        input: part.tool_input as Record<string, unknown> | undefined,
        output: part.tool_output as string | undefined,
        durationMs: part.duration_ms as number | undefined,
      },
    ];
  return [];
}

/** Restore group IDs at the API boundary; the component never inspects server DTOs. */
export function readNativeMessages(
  source: readonly RawMessage[],
): VikingBotMessage[] {
  return source.map((message) => {
    let group = 0;
    let active: string | undefined;
    const parts = message.parts?.flatMap(readPart) ?? [
      { type: 'text' as const, text: message.content ?? '' },
    ];
    return {
      id: message.id,
      role: message.role,
      createdAt: message.created_at,
      turnId: message.turn_id ?? message.id,
      parts: parts.map((part) => {
        if (part.type === 'reasoning' || part.type === 'tool') {
          active ??= `${message.id}:activity:${group++}`;
          return { ...part, groupId: active };
        }
        active = undefined;
        return message.role === 'user' && part.type === 'text'
          ? { ...part, format: 'plain' as const }
          : part;
      }),
    };
  });
}

/** Keep the existing native persistence contract: transient reasoning is not stored. */
export function writeNativeParts(
  parts: readonly VikingBotMessagePart[],
): RawPart[] {
  return parts.flatMap((part): RawPart[] => {
    if (part.type === 'text') return [{ type: 'text', text: part.text }];
    if (part.type === 'image')
      return [
        {
          type: 'image_url',
          image_url: { url: part.src, detail: part.detail },
        },
      ];
    if (part.type === 'context')
      return [
        {
          type: 'context',
          uri: part.uri,
          context_type: part.contextType,
          abstract: part.abstract,
        },
      ];
    if (part.type === 'tool')
      return [
        {
          type: 'tool',
          tool_id: part.id,
          tool_name: part.name,
          tool_status: part.status,
          tool_input: part.input,
          tool_output: part.output,
          duration_ms: part.durationMs,
        },
      ];
    return [];
  });
}
