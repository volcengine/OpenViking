import { useLocale } from '../../../shared/locale-context.js';
import type { VikingBotInteractionFieldContext } from '../../../components/viking-bot/interaction/field-types.js';
import { ChoiceInput } from '../../../components/viking-bot/interaction/choice-input.js';
import { resolveInteractionLabels } from '../../../components/viking-bot/interaction/labels.js';
import { hitlLabels } from './locales.js';

export function TargetField({
  field,
  question,
  recommendations,
}: {
  field: VikingBotInteractionFieldContext<string>;
  question: string;
  recommendations: readonly string[];
}) {
  const locale = useLocale();
  const placeholder = hitlLabels(locale).customDirectory;
  return (
    <ChoiceInput
      controlProps={field.controlProps}
      request={{
        kind: 'choice',
        question,
        options: recommendations.map((uri) => ({ value: uri, label: uri })),
        allowCustomInput: { placeholder },
      }}
      value={field.value}
      labels={{ ...resolveInteractionLabels(locale), other: placeholder }}
      locale={locale}
      disabled={field.disabled}
      invalid={Boolean(field.error)}
      errorId={field.controlProps['aria-describedby'] ?? ''}
      onChange={field.onChange}
    />
  );
}
