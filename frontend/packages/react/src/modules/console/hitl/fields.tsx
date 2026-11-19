import type {
  VikingBotInteractionFields,
  VikingBotInteractionProps,
} from '../../../components/viking-bot/index.js';
import type { VikingBotInteractionFieldContext } from '../../../components/viking-bot/interaction/field-types.js';
import { retainInactiveFieldRenderer } from '../../../components/viking-bot/interaction/field-renderer.js';
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
import {
  compileTargetError,
  overlapsSource,
  parseCompileUri,
  parseSourceUris,
} from './uris.js';

function FieldError({
  code,
}: {
  code:
    | 'required'
    | 'uriInvalid'
    | 'nameInvalid'
    | 'targetNotDirectory'
    | 'targetReadOnly'
    | 'targetCheckFailed'
    | 'targetOverlap'
    | 'targetNamespaceRoot'
    | 'targetSkillNamespace';
}) {
  return hitlLabels(useLocale())[code];
}

function SourceUriHint() {
  return hitlLabels(useLocale()).sourceUriHint;
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

function cardFields(
  card: HitlCard,
  scope: CompilePickerScope,
  title: string,
  recommendations: readonly string[],
  suggested: readonly string[],
  sources: readonly string[],
  userId?: string,
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
    cardType === 'compile_source' && request.kind === 'text' && scope.client
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
        : cardType === 'compile_skill' &&
            request.kind === 'choice' &&
            scope.client
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
      render: render && retainInactiveFieldRenderer(render),
      ...(cardType === 'compile_source' && !scope.client
        ? {
            description: <SourceUriHint />,
          }
        : {}),
      ...fields?.value,
      validate: async (value, answer, context) => {
        if (required && !value.trim()) return <FieldError code="required" />;
        if (cardType === 'compile_name' && !validCompileName(value))
          return <FieldError code="nameInvalid" />;
        if (cardType === 'compile_source' && !parseSourceUris(value)?.length)
          return <FieldError code="uriInvalid" />;
        const singleUri =
          cardType === 'compile_target' || cardType === 'compile_skill';
        const path = singleUri ? parseCompileUri(value) : undefined;
        if (singleUri && !path) return <FieldError code="uriInvalid" />;
        if (cardType === 'compile_target' && path) {
          const namespaceError = compileTargetError(path);
          if (namespaceError) return <FieldError code={namespaceError} />;
          if (overlapsSource(path, sources, userId))
            return <FieldError code="targetOverlap" />;
          if (scope.client) {
            const checked = await checkDestination(
              scope.client,
              path,
              scope.requestOptions,
              context.signal,
            );
            if (checked && 'error' in checked)
              return <FieldError code={checked.error} />;
            const checkedNamespaceError =
              checked && compileTargetError(checked.uri);
            if (checkedNamespaceError)
              return <FieldError code={checkedNamespaceError} />;
            if (checked && overlapsSource(checked.uri, sources, userId))
              return <FieldError code="targetOverlap" />;
          }
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
  sources: readonly string[],
  userId?: string,
): Pick<VikingBotInteractionProps, 'request' | 'fields' | 'className'> {
  if (card.options?.createRequest)
    return { request: card.request, fields: card.options.fields };
  const title =
    hitlLabels(locale).titles[card.cardType as CompileCardType] ?? card.title;
  const request =
    card.request.kind === 'steps'
      ? card.request
      : { ...card.request, question: title };
  if (card.cardType === 'compile_target' && request.kind === 'text') {
    const suggested = parseCompileUri(request.initialValue ?? '');
    request.initialValue =
      suggested && recommendations.includes(suggested)
        ? suggested
        : recommendations[0];
  }
  return {
    request,
    fields: cardFields(
      card,
      scope,
      title,
      recommendations,
      suggested,
      sources,
      userId,
    ),
    className: `ov-viking-bot__compile-card ov-viking-bot__compile-card--${card.cardType}`,
  };
}
