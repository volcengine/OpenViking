import type { AskHumanField, AskHumanValue } from '@ark-design/ui/ask-human';

import type {
  VikingBotInteractionAnswer,
  VikingBotInteractionRequest,
} from './types.js';

/** 把请求转成 AskHuman 的字段描述。 */
export const toFields = (
  request: VikingBotInteractionRequest,
): AskHumanField[] => {
  if (request.kind === 'choice') {
    return [
      {
        type: 'single-select',
        field: 'value',
        options: request.options.map((option) => ({
          value: option.value,
          label: option.label,
          description: option.description,
        })),
        allowCustomInput: request.allowCustomInput,
      },
    ];
  }
  if (request.kind === 'repair') {
    return [
      {
        type: 'single-select',
        field: 'value',
        options: request.options.map((option) => ({
          value: option.id,
          label: option.label,
          description: option.description,
        })),
      },
    ];
  }
  return [
    { type: `viking-bot-${request.kind}`, field: 'value', extra: { request } },
  ];
};

/** 把 AskHuman 的提交值转回交互答案。 */
export const toAnswer = (
  request: VikingBotInteractionRequest,
  value: AskHumanValue,
): VikingBotInteractionAnswer => {
  const raw = value.value;
  if (request.kind === 'choice' || request.kind === 'repair') {
    const selected = raw as {
      type?: string;
      value?: string;
      customText?: string;
    };
    return {
      kind: request.kind,
      value:
        selected?.type === 'custom'
          ? (selected.customText ?? '')
          : (selected?.value ?? ''),
    };
  }
  if (request.kind === 'text') {
    return { kind: 'text', value: typeof raw === 'string' ? raw : '' };
  }
  if (request.kind === 'schedule') {
    return { kind: 'schedule', value: raw as never };
  }
  return { kind: 'custom', value: raw };
};
