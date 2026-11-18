import type {
  VikingBotInteractionFields,
  VikingBotInteractionProps,
} from '../../../components/viking-bot/index.js';
import type { VikingBotInteractionFieldContext } from '../../../components/viking-bot/interaction/field-types.js';
import { useLocale } from '../../../shared/locale-context.js';
import type { Locale } from '../../../shared/locale.js';
import type { HitlCard } from './types.js';
import { hitlLabels, type CompileCardType } from './locales.js';
import {
  SkillField,
  SourceField,
  type CompilePickerScope,
} from './picker-fields.js';
import { TargetField } from './target-field.js';
import { checkDestination } from './directory-validation.js';

function FieldError({
  code,
}: {
  code:
    | 'required'
    | 'uriInvalid'
    | 'nameInvalid'
    | 'targetNotDirectory'
    | 'targetReadOnly'
    | 'targetCheckFailed';
}) {
  return hitlLabels(useLocale())[code];
}

function validCompileName(name: string) {
  if (name !== name.trim()) return false;
  return (
    /^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$/.test(name) ||
    /^[A-Za-z0-9_〇\p{Unified_Ideograph}][A-Za-z0-9_〇\p{Unified_Ideograph}-]{0,31}$/u.test(
      name,
    )
  );
}

function validUri(uri: string) {
  if (!/^viking:\/\/[^\s?#\\]+$/.test(uri)) return false;
  try {
    const path = decodeURIComponent(uri.slice('viking://'.length));
    const segments = path.replace(/\/$/, '').split('/');
    return (
      !Array.from(path).some((character) => {
        const code = character.charCodeAt(0);
        return code < 32 || code === 127 || character === '\\';
      }) &&
      segments.every(
        (segment) => segment && segment !== '.' && segment !== '..',
      )
    );
  } catch {
    return false;
  }
}

function cardFields(
  card: HitlCard,
  scope: CompilePickerScope,
  title: string,
  recommendations: readonly string[],
  suggested: readonly string[],
): VikingBotInteractionFields | undefined {
  const fields = card.options?.fields;
  if (card.options?.createRequest || card.request.kind === 'schedule')
    return fields;
  const { cardType, request } = card;
  const uri = ['compile_source', 'compile_target', 'compile_skill'].includes(
    cardType,
  );
  const required = uri || cardType === 'compile_name';
  const hideFieldLabel =
    request.kind === 'text' &&
    ['compile_source', 'compile_name', 'compile_target'].includes(cardType);
  const render =
    cardType === 'compile_source' && request.kind === 'text'
      ? (field: VikingBotInteractionFieldContext<string>) => (
          <SourceField field={field} scope={scope} suggested={suggested} />
        )
      : cardType === 'compile_target' && request.kind === 'text'
        ? (field: VikingBotInteractionFieldContext<string>) => (
            <TargetField
              field={field}
              question={title}
              recommendations={recommendations}
            />
          )
        : cardType === 'compile_skill' && request.kind === 'choice'
          ? (field: VikingBotInteractionFieldContext<string>) => (
              <SkillField field={field} scope={scope} request={request} />
            )
          : undefined;
  return {
    ...fields,
    value: {
      ...(hideFieldLabel
        ? {
            label: (
              <span className="ov-viking-bot__interaction-sr">{title}</span>
            ),
          }
        : {}),
      render,
      ...fields?.value,
      validate: async (value, answer, context) => {
        if (required && !value.trim()) return <FieldError code="required" />;
        if (cardType === 'compile_name' && !validCompileName(value))
          return <FieldError code="nameInvalid" />;
        if (
          uri &&
          value
            .split(/\r?\n/)
            .some((item) => item.trim() && !validUri(item.trim()))
        )
          return <FieldError code="uriInvalid" />;
        if (cardType === 'compile_target' && scope.client) {
          const error = await checkDestination(
            scope.client,
            value,
            scope.requestOptions,
            context.signal,
          );
          if (error) return <FieldError code={error} />;
        }
        return fields?.value?.validate?.(value, answer, context);
      },
    },
  };
}

export function presentCard(
  card: HitlCard,
  locale: Locale,
  scope: CompilePickerScope,
  recommendations: readonly string[],
  suggested: readonly string[],
): Pick<VikingBotInteractionProps, 'request' | 'fields' | 'className'> {
  if (card.options?.createRequest)
    return { request: card.request, fields: card.options.fields };
  const title =
    hitlLabels(locale).titles[card.cardType as CompileCardType] ?? card.title;
  const request =
    card.request.kind === 'steps'
      ? card.request
      : { ...card.request, question: title };
  if (card.cardType === 'compile_target' && request.kind === 'text')
    request.initialValue ??= recommendations[0];
  return {
    request,
    fields: cardFields(card, scope, title, recommendations, suggested),
    className: `ov-viking-bot__compile-card ov-viking-bot__compile-card--${card.cardType}`,
  };
}
