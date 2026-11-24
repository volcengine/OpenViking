import type { OvClient, OvRequestOptions } from '../../../client/types.js';
import type {
  VikingBotConfirmationRow,
  VikingBotInteractionAnswer,
} from '../../../components/viking-bot/index.js';
import { resolveInteractionLabels } from '../../../components/viking-bot/interaction/labels.js';
import type { VikingBotMessage } from '../../../components/viking-bot/model.js';
import type { Locale } from '../../../shared/locale.js';
import type { HitlGroup } from './types.js';
import {
  checkDestination,
  statCompileLocation,
} from './directory-validation.js';
import {
  compileTargetError,
  formatCompileUriLabel,
  overlapsSource,
  parseCompileUri,
  parseSourceUris,
  validCompileName,
} from './uris.js';
import { hitlLabels, type CompileCardType } from './locales.js';
import { object, parse } from './json.js';

type Answers = Record<string, VikingBotInteractionAnswer>;

/** Review complete Wiki suggestions without bypassing a question the tool still needs answered. */
export function wikiConfirmation(
  group: HitlGroup,
  turn: VikingBotMessage | undefined,
  locale: Locale,
  userId?: string,
) {
  const userInput = turn?.parts.find((part) => part.type === 'text');
  if (
    userInput?.type === 'text' &&
    object(parse(userInput.text))?.type === 'request_user_input_response'
  )
    return;

  const required = new Set([
    'compile_name',
    'compile_source',
    'compile_skill',
    'compile_target',
  ]);
  const seen = new Set<string>();
  const answers: Answers = {};
  const rows: VikingBotConfirmationRow[] = [];
  const labels = hitlLabels(locale);
  let sources: string[] = [];
  let skill = '';
  let target = '';
  for (const card of group.cards) {
    const { cardType, request, payload } = card;
    if (card.options || seen.has(cardType)) return;
    seen.add(cardType);
    let answer: VikingBotInteractionAnswer | undefined;
    let display: string | undefined;
    if (cardType === 'compile_name' && request.kind === 'text') {
      const name = request.initialValue;
      if (name && validCompileName(name))
        answer = { kind: 'text', value: name };
    } else if (cardType === 'compile_source' && request.kind === 'text') {
      const supplied = Array.isArray(payload.suggested_values)
        ? payload.suggested_values
        : [];
      const refs =
        turn?.parts.flatMap((part) =>
          part.type === 'context' && part.contextType === 'resource'
            ? [part.uri]
            : [],
        ) ?? [];
      const selected = parseSourceUris(
        (supplied.length ? supplied : refs).join('\n'),
      );
      if (selected?.length) {
        sources = selected;
        answer = { kind: 'text', value: selected.join('\n') };
        display = selected.map(formatCompileUriLabel).join('\n');
      }
    } else if (cardType === 'compile_skill' && request.kind === 'choice') {
      const selected =
        request.options.find(
          (option) => option.value === payload.suggested_value,
        ) ?? (request.options.length === 1 ? request.options[0] : undefined);
      const uri = selected && parseCompileUri(selected.value);
      if (uri && /(?:^|\/)llm-wiki(?:\/SKILL\.md)?$/.test(uri)) {
        skill = uri;
        answer = { kind: 'choice', value: selected.value };
        display =
          typeof selected.label === 'string'
            ? selected.label
            : formatCompileUriLabel(uri);
      }
    } else if (cardType === 'compile_target' && request.kind === 'text') {
      const uri = parseCompileUri(request.initialValue ?? '');
      if (uri && !compileTargetError(uri)) {
        target = uri;
        answer = { kind: 'text', value: uri };
        display = formatCompileUriLabel(uri);
      }
    } else if (cardType === 'compile_instruction' && request.kind === 'text') {
      answer = { kind: 'text', value: request.initialValue ?? '' };
    } else if (cardType === 'compile_schedule' && request.kind === 'schedule') {
      const value = request.initialValue;
      const interaction = resolveInteractionLabels(locale);
      answer = { kind: 'schedule', value };
      display =
        value.mode === 'none'
          ? interaction.scheduleNone
          : value.mode === 'daily'
            ? labels.daily(value.time)
            : labels.weekly(
                interaction.weekdays[value.weekday - 1]!,
                value.time,
              );
      if (value.mode !== 'none' && request.timezone)
        display += ` (${request.timezone})`;
    }
    if (!answer) return;
    answers[card.id] = answer;
    rows.push({
      label: labels.titles[cardType as CompileCardType],
      value:
        (display ?? (typeof answer.value === 'string' ? answer.value : '')) ||
        labels.empty,
    });
  }
  if (
    [...required].some((type) => !seen.has(type)) ||
    overlapsSource(target, sources, userId)
  )
    return;
  return { answers, rows, sources, target, skill };
}

export async function validateWikiConfirmation(
  defaults: NonNullable<ReturnType<typeof wikiConfirmation>>,
  client: OvClient,
  options: OvRequestOptions | undefined,
  signal: AbortSignal,
  locale: Locale,
  userId?: string,
) {
  const labels = hitlLabels(locale);
  const request = { ...options, signal };
  const [sources, skill, target] = await Promise.all([
    Promise.all(
      defaults.sources.map((uri) =>
        statCompileLocation(client, uri, request).catch(() => undefined),
      ),
    ),
    statCompileLocation(client, defaults.skill, request).catch(() => undefined),
    checkDestination(client, defaults.target, options, signal),
  ]);
  signal.throwIfAborted();
  if (
    sources.some(
      (source) =>
        !source?.isDirectory || ['denied', 'container'].includes(source.access),
    )
  )
    throw new Error(labels.sourceCheckFailed);
  if (
    !skill ||
    ['denied', 'container'].includes(skill.access) ||
    (!skill.isDirectory && !defaults.skill.endsWith('/SKILL.md'))
  )
    throw new Error(labels.skillCheckFailed);
  if (!target) throw new Error(labels.targetCheckFailed);
  if ('error' in target) throw new Error(labels[target.error]);
  const canonicalError = compileTargetError(target.uri);
  if (canonicalError) throw new Error(labels[canonicalError]);
  if (
    overlapsSource(
      target.uri,
      sources.map((source) => source!.uri),
      userId,
    )
  )
    throw new Error(labels.targetOverlap);
}
