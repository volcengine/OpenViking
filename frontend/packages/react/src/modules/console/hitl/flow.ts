import type { VikingBotInteractionAnswer } from '../../../components/viking-bot/index.js';
import {
  compileTargetError,
  formatCompileUriLabel,
  parseCompileUri,
  parseSourceUris,
} from './uris.js';
import type { HitlCard, HitlGroup } from './types.js';

type Answers = Readonly<Record<string, VikingBotInteractionAnswer>>;

function skillOutput(uri: string) {
  if (/(?:^|\/)daily-report(?:\/SKILL\.md)?\/?$/.test(uri)) return 'markdown';
  if (/(?:^|\/)llm-wiki(?:\/SKILL\.md)?\/?$/.test(uri)) return 'wiki';
}

function requestedOutput(group: HitlGroup, answers: Answers) {
  const type = group.cards.find(
    (card) => card.cardType === 'compile_output_type',
  );
  const chosen = type && answers[type.id];
  if (type) return chosen?.kind === 'choice' ? chosen.value : undefined;
  const skill = group.cards.find((card) => card.cardType === 'compile_skill');
  const skillAnswer = skill && answers[skill.id];
  if (skillAnswer?.kind === 'choice') return skillOutput(skillAnswer.value);
  if (skill?.request.kind === 'choice' && skill.request.options.length === 1)
    return skillOutput(skill.request.options[0]!.value);
}

export function clarificationFlow(group: HitlGroup, answers: Answers) {
  const output = requestedOutput(group, answers);
  const cards = [...group.cards];
  const target = cards.findIndex(
    (card) =>
      !card.options?.createRequest && card.cardType === 'compile_target',
  );
  if (target !== -1) {
    // The destination uses the confirmed output type, task name and source directories.
    const dependencies = cards.splice(target + 1);
    const beforeTarget: HitlCard[] = [];
    const afterTarget: HitlCard[] = [];
    for (const card of dependencies) {
      const required =
        !card.options?.createRequest &&
        (card.cardType === 'compile_name' ||
          card.cardType === 'compile_source' ||
          card.cardType === 'compile_output_type');
      (required ? beforeTarget : afterTarget).push(card);
    }
    cards.splice(target, 0, ...beforeTarget);
    cards.push(...afterTarget);
  }
  return { cards, output };
}

export function confirmedSources(group: HitlGroup, answers: Answers) {
  return group.cards.flatMap((card) => {
    if (
      card.cardType !== 'compile_source' ||
      card.options?.createRequest ||
      card.options?.serialize
    )
      return [];
    const answer = answers[card.id];
    return answer?.kind === 'text' ? (parseSourceUris(answer.value) ?? []) : [];
  });
}

function outputSuffix(output?: string) {
  if (output === 'markdown' || output === 'daily_report')
    return '_daily_report';
  if (output === 'wiki' || output === 'report') return `_${output}`;
  return '';
}

export function outputDirectories(
  card: HitlCard,
  group: HitlGroup,
  answers: Answers,
  output?: string,
  userId?: string,
) {
  if (card.cardType !== 'compile_target' || card.request.kind !== 'text')
    return [];
  const name = group.cards.find((item) => item.cardType === 'compile_name');
  const answer = name && answers[name.id];
  const stem = answer?.kind === 'text' ? answer.value.trim() : '';
  const suffix = outputSuffix(output);
  const path =
    suffix && stem && stem !== '.' && stem !== '..' && !/[\\/]/.test(stem)
      ? '/' + stem + (stem.endsWith(suffix) ? '' : suffix)
      : '';
  const personal = userId
    ? `viking://user/${userId}/resources`
    : 'viking://~/resources';
  const suggested = parseCompileUri(card.request.initialValue ?? '');
  const type = group.cards.find(
    ({ cardType }) => cardType === 'compile_output_type',
  );
  const suggestedType =
    type?.request.kind === 'choice' ? type.request.initialValue : undefined;
  const suggestedSuffix =
    suggested?.match(/_(?:daily_report|report|wiki)$/)?.[0] ??
    outputSuffix(suggestedType);
  const mismatch = suffix && suggestedSuffix && suffix !== suggestedSuffix;
  const existing =
    suggested && !mismatch && !compileTargetError(suggested)
      ? suggested
      : undefined;
  const generated = path
    ? [`viking://resources${path}`, `${personal}${path}`]
    : [];
  const personalAlias = userId ? `viking://user/${userId}/` : undefined;
  const comparisonLabel = (uri: string) =>
    formatCompileUriLabel(
      personalAlias && uri.startsWith('viking://~/')
        ? personalAlias + uri.slice('viking://~/'.length)
        : uri,
    );
  const candidates = existing
    ? [
        ...generated.map((uri) =>
          comparisonLabel(uri) === comparisonLabel(existing) ? existing : uri,
        ),
        existing,
      ]
    : generated;
  return [...new Set(candidates)];
}
