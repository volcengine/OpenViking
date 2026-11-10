import type { AskHumanField, AskHumanValue } from '@ark-design/ui/ask-human';

import type {
  VikingBotInteractionAnswer,
  VikingBotInteractionRequest,
} from './types.js';

/** 把请求转成 AskHuman 的字段描述。 */
export const toFields = (
  request: VikingBotInteractionRequest,
  other: string,
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
        allowCustomInput: request.allowCustomInput
          ? {
              placeholder:
                typeof request.allowCustomInput === 'object'
                  ? (request.allowCustomInput.placeholder ?? other)
                  : other,
            }
          : false,
      },
    ];
  }
  if (request.kind === 'repair') {
    return [
      {
        type: 'single-select',
        field: 'value',
        // Ark 的 SingleSelectList 用 useState 初始化 activeIndex，只在首次渲染
        // 读一次值；而 AskHuman 的 defaultValue 要等 Effect 才写进 Form，那时
        // 已晚一步。因此初始值必须由 Form.Item 在首次渲染就注入，否则显示与
        // 提交值会不一致。
        formItemProps: request.initialValue
          ? {
              initialValue: {
                type: 'option',
                value: request.initialValue,
              },
            }
          : undefined,
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

/** 按 request 计算 AskHuman 的初始答案，保证预填值与提交值一致。 */
export const toDefaultValue = (
  request: VikingBotInteractionRequest,
): AskHumanValue | undefined => {
  if (request.kind === 'choice') {
    return undefined;
  }
  if (request.kind === 'repair') {
    // Ark 的 single-select 值是 { type, value } 对象，直接传字符串不会被选中。
    return request.initialValue
      ? { value: { type: 'option', value: request.initialValue } }
      : undefined;
  }
  if (request.kind === 'text') {
    return { value: request.initialValue ?? '' };
  }
  if (request.kind === 'schedule') {
    return { value: request.initialValue };
  }
  return undefined;
};

/** 选择类问题必须选中有效选项；其他类型按自身初始值即可提交。 */
export const isValidAnswer = (
  request: VikingBotInteractionRequest,
  value: AskHumanValue,
): boolean => {
  const raw = value.value;
  if (request.kind === 'choice') {
    if (!raw || typeof raw !== 'object') return false;
    const selected = raw as {
      type?: string;
      value?: string;
      customText?: string;
    };
    if (selected.type === 'custom') return Boolean(selected.customText?.trim());
    return request.options.some((option) => option.value === selected.value);
  }
  if (request.kind === 'repair') {
    const selected = raw as { type?: string; value?: string } | undefined;
    if (!selected || typeof selected !== 'object') return false;
    return request.options.some((option) => option.id === selected.value);
  }
  return true;
};
