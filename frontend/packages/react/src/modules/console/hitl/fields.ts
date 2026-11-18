import { createElement } from 'react';
import { useLocale } from '../../../shared/locale-context.js';
import type { VikingBotInteractionFields } from '../../../components/viking-bot/index.js';
import type { HitlCard } from './types.js';
import type { Locale } from '../../../shared/locale.js';
import { hitlLabels } from './locales.js';

function FieldError({ code }: { code: 'required' | 'uriInvalid' }) {
  return hitlLabels(useLocale())[code];
}

export function cardFields(
  card: HitlCard,
  locale: Locale,
): VikingBotInteractionFields | undefined {
  const fields = card.options?.fields;
  if (card.options?.createRequest || card.request.kind === 'schedule')
    return fields;
  const labels = hitlLabels(locale);
  const uri = ['compile_source', 'compile_target', 'compile_skill'].includes(
    card.cardType,
  );
  const required = uri || card.cardType === 'compile_name';
  return {
    ...fields,
    value: {
      ...(card.cardType === 'compile_source'
        ? { description: labels.uriLines }
        : {}),
      ...fields?.value,
      validate: async (value, answer, context) => {
        if (required && !value.trim())
          return createElement(FieldError, { code: 'required' });
        if (
          uri &&
          value
            .split(/\r?\n/)
            .some(
              (item) => item.trim() && !/^viking:\/\/\S+$/.test(item.trim()),
            )
        )
          return createElement(FieldError, { code: 'uriInvalid' });
        return fields?.value?.validate?.(value, answer, context);
      },
    },
  };
}
