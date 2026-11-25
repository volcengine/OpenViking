import type { VikingBotQuickInput } from '../../components/viking-bot/capabilities.js';
import type { VikingBotProps } from '../../components/viking-bot/types.js';
import { VikingBotIcon } from '../../icons/viking-bot-icons.js';
import type { Locale } from '../../shared/locale.js';
import { resolveConsoleLocale } from './labels.js';

const builtInPrompts = new Set(
  (['en-US', 'zh-CN'] as const).flatMap((locale) =>
    Object.values(resolveConsoleLocale(locale).compileQuickInputs).map(
      ({ prompt }) => prompt,
    ),
  ),
);

export function compileQuickInputProps({
  conversation,
  locale,
  managedDraft,
  busy,
  setDraft,
}: {
  conversation?: Partial<VikingBotProps>;
  locale: Locale;
  managedDraft: string;
  busy: boolean;
  setDraft: (value: string) => void;
}): Pick<VikingBotProps, 'quickInputs' | 'onQuickInputSelect'> {
  if (conversation?.quickInputs !== undefined)
    return {
      quickInputs: conversation.quickInputs,
      onQuickInputSelect: conversation.onQuickInputSelect,
    };
  const copy = resolveConsoleLocale(locale).compileQuickInputs;
  const quickInputs: readonly VikingBotQuickInput[] = [
    {
      id: 'create-wiki',
      ...copy.createWiki,
      icon: (
        <VikingBotIcon
          name="book"
          width={14}
          height={14}
          viewBox="-0.833333 -2.357143 16 16"
          strokeWidth={8 / 7}
        />
      ),
    },
    {
      id: 'daily-report',
      ...copy.dailyReport,
      icon: <VikingBotIcon name="report" width={14} height={14} />,
    },
  ];
  return {
    quickInputs,
    onQuickInputSelect:
      conversation?.onQuickInputSelect ??
      ((input) => {
        const draft = conversation?.draft ?? managedDraft;
        if (busy || draft === input.prompt) return;
        if (draft && !builtInPrompts.has(draft)) return;
        if (conversation?.draft === undefined) setDraft(input.prompt);
        conversation?.onDraftChange?.(input.prompt);
      }),
  };
}
