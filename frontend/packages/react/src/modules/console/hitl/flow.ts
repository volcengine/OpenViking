import type { VikingBotMessage } from '../../../components/viking-bot/model.js';
import type { VikingBotInteractionAnswer } from '../../../components/viking-bot/index.js';
import { resolveConsoleLocale } from '../labels.js';
import type { HitlCard, HitlGroup } from './types.js';

type Answers = Readonly<Record<string, VikingBotInteractionAnswer>>;

const prompts = (['en-US', 'zh-CN'] as const).map(
  (locale) => resolveConsoleLocale(locale).compileQuickInputs,
);
function skillOutput(uri: string) {
  if (/(?:^|\/)daily-report(?:\/SKILL\.md)?\/?$/.test(uri)) return 'markdown';
  if (/(?:^|\/)llm-wiki(?:\/SKILL\.md)?\/?$/.test(uri)) return 'wiki';
}

function requestedOutput(
  group: HitlGroup,
  lastUser?: VikingBotMessage,
  answers?: Answers,
) {
  const prompt = lastUser?.parts.find((part) => part.type === 'text')?.text;
  const type = group.cards.find(
    (card) => card.cardType === 'compile_output_type',
  );
  const chosen = type && answers?.[type.id];
  if (chosen?.kind === 'choice') return chosen.value;
  if (prompts.some((items) => items.dailyReport.prompt === prompt))
    return 'markdown';
  if (prompts.some((items) => items.createWiki.prompt === prompt))
    return 'wiki';
  const skill = group.cards.find((card) => card.cardType === 'compile_skill');
  const skillAnswer = skill && answers?.[skill.id];
  if (skillAnswer?.kind === 'choice') return skillOutput(skillAnswer.value);
  if (skill?.request.kind === 'choice' && skill.request.options.length === 1)
    return skillOutput(skill.request.options[0]!.value);
}

export function clarificationFlow(
  group: HitlGroup,
  answers: Answers,
  lastUser?: VikingBotMessage,
) {
  const output = requestedOutput(group, lastUser, answers);
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
  const name = cards.findIndex(
    (card) => !card.options?.createRequest && card.cardType === 'compile_name',
  );
  if (target >= 0 && name > target) {
    const [nameCard] = cards.splice(name, 1);
    if (nameCard) cards.splice(target, 0, nameCard);
  }
  return { cards, fixed, output };
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
  const suggested = card.request.initialValue;
  if (!path && !suggested) return ['viking://resources', personal];
  return [
    ...new Set([
      ...(path ? [`viking://resources${path}`, `${personal}${path}`] : []),
      ...(suggested ? [suggested] : []),
    ]),
  ];
}
