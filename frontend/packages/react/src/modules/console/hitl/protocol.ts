import { mergeToolResults } from '../../../components/viking-bot/ark-adapter.js';
import type {
  VikingBotMessage,
  VikingBotToolPart,
} from '../../../components/viking-bot/model.js';
import { builtinRequest } from './requests.js';
import type { ConsoleHitlOptions, HitlCard, HitlGroup } from './types.js';

import { object, parse, text } from './json.js';
export { object, parse, text, weekdays } from './json.js';

export function resultBody(output: unknown) {
  const body = object(parse(output));
  if (!body || body.isError === true || body.is_error === true) return;
  if ('structuredContent' in body) return object(body.structuredContent);
  if ('schema_version' in body) return body;
  if (Array.isArray(body.content))
    return object(
      parse(
        body.content
          .map(object)
          .filter((item) => item?.type === 'text')
          .map((item) => (typeof item?.text === 'string' ? item.text : ''))
          .join(''),
      ),
    );
}

export function isInputTool(tool: VikingBotToolPart) {
  if (tool.server && tool.server !== 'openviking-mcp') return false;
  return (
    tool.name === 'mcp_openviking-mcp_request_user_input' ||
    tool.name === 'request_user_input' ||
    tool.name === 'openviking-mcp.request_user_input'
  );
}

export function readGroup(
  output: unknown,
  id: string,
  options?: ConsoleHitlOptions,
): HitlGroup | undefined {
  const body = resultBody(output);
  if (
    !id ||
    body?.schema_version !== '1.0' ||
    !Array.isArray(body.cards) ||
    !body.cards.length
  )
    return;
  const cards: HitlCard[] = [];
  for (const [index, raw] of body.cards.entries()) {
    const item = object(raw);
    const payload = object(item?.payload);
    if (!item || !text(item.card_type) || !text(item.title) || !payload) return;
    const card = {
      id: `${id}:${index}`,
      cardType: item.card_type,
      title: item.title,
      payload,
    };
    const config =
      options?.configureCard?.(card) ?? options?.cards?.[card.cardType];
    const request = config?.createRequest?.(card) ?? builtinRequest(card);
    if (!request) return;
    cards.push({ ...card, request, options: config });
  }
  return { id, cards };
}

export function groupsIn(
  messages: readonly VikingBotMessage[],
  options?: ConsoleHitlOptions,
) {
  return messages.flatMap((message) =>
    message.role !== 'assistant' || message.streaming || message.clientError
      ? []
      : mergeToolResults(message.parts).flatMap((part) => {
          if (
            part.type !== 'tool' ||
            part.status !== 'completed' ||
            !isInputTool(part)
          )
            return [];
          const group = readGroup(part.output, part.id, options);
          return group ? [group] : [];
        }),
  );
}

export function pendingGroup(
  messages: readonly VikingBotMessage[],
  options?: ConsoleHitlOptions,
) {
  let lastUser = -1;
  messages.forEach((message, index) => {
    if (message.role === 'user') lastUser = index;
  });
  return groupsIn(messages.slice(lastUser + 1), options).at(-1);
}
