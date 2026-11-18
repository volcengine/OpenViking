import type { VikingBotInteractionAnswer } from '../../../components/viking-bot/index.js';
import {
  compileTargetError,
  parseCompileUri,
  parseSourceUris,
} from './uris.js';
import type { HitlCard, HitlGroup } from './types.js';

type Answers = Readonly<Record<string, VikingBotInteractionAnswer>>;

function skillOutput(uri: string) {
  if (/(?:^|\/)daily-report(?:\/SKILL\.md)?\/?$/.test(uri)) return 'markdown';
  if (/(?:^|\/)llm-wiki(?:\/SKILL\.md)?\/?$/.test(uri)) return 'wiki';
}

function requestedOutput(group: HitlGroup, answers?: Answers) {
  const type = group.cards.find(
    (card) => card.cardType === 'compile_output_type',
  );
  const chosen = type && answers?.[type.id];
  if (chosen?.kind === 'choice') return chosen.value;
  const skill = group.cards.find((card) => card.cardType === 'compile_skill');
  const skillAnswer = skill && answers?.[skill.id];
  if (skillAnswer?.kind === 'choice') return skillOutput(skillAnswer.value);
  if (skill?.request.kind === 'choice' && skill.request.options.length === 1)
    return skillOutput(skill.request.options[0]!.value);
}

export function clarificationFlow(group: HitlGroup, answers: Answers) {
  const output = requestedOutput(group, answers);
  const fixed: Record<string, VikingBotInteractionAnswer> = {};
  const cards = group.cards.filter((card) => {
    if (
      group.cards.length > 1 &&
      !card.options &&
      card.cardType === 'compile_output_type' &&
      card.request.kind === 'choice' &&
      output &&
      card.request.options.some(({ value }) => value === output)
    ) {
      fixed[card.id] = { kind: 'choice', value: output };
      return false;
    }
    return true;
  });
  const target = cards.findIndex(
    (card) =>
      !card.options?.createRequest && card.cardType === 'compile_target',
  );
  if (target !== -1) {
    // Both the recommended path and the overlap check depend on earlier answers.
    const dependencies = cards.splice(target + 1);
    const beforeTarget: HitlCard[] = [];
    const afterTarget: HitlCard[] = [];
    for (const card of dependencies) {
      const required =
        !card.options?.createRequest &&
        (card.cardType === 'compile_name' ||
          card.cardType === 'compile_source');
      (required ? beforeTarget : afterTarget).push(card);
    }
    cards.splice(target, 0, ...beforeTarget);
    cards.push(...afterTarget);
  }
  return { cards, fixed, output };
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
  const suffix =
    output === 'markdown' ? '_daily_report' : output === 'wiki' ? '_wiki' : '';
  const path =
    suffix && stem && stem !== '.' && stem !== '..' && !/[\\/]/.test(stem)
      ? '/' + encodeURIComponent(stem) + (stem.endsWith(suffix) ? '' : suffix)
      : '';
  const personal = userId
    ? `viking://user/${encodeURIComponent(userId)}/resources`
    : 'viking://~/resources';
  const suggested = parseCompileUri(card.request.initialValue ?? '');
  return [
    ...new Set([
      ...(path ? [`viking://resources${path}`, `${personal}${path}`] : []),
      ...(suggested && !compileTargetError(suggested) ? [suggested] : []),
    ]),
  ];
}
