import { Button } from '../../shared/components/button/button.js';
import type { Locale } from '../../shared/locale.js';
import type { RefObject } from 'react';
import type { SenderInstance } from '@ark-design/ui/sender';
import { Tooltip } from '../../shared/components/tooltip/index.js';
import type {
  VikingBotReferences,
  VikingBotReference,
} from './capabilities.js';
import type { VikingBotLabels } from './model.js';
import { BotPopover } from './bot-popover.js';
import { ReferencePicker } from './reference-picker.js';
import type { MentionConfig } from './reference-picker.js';

export function selectReference(
  config: VikingBotReferences,
  item: VikingBotReference,
) {
  if (!config.selected.some((entry) => entry.uri === item.uri))
    config.onChange([
      ...config.selected,
      { uri: item.uri, name: item.name, type: item.type },
    ]);
}

export function composerMention(
  config: VikingBotReferences | undefined,
  disabled: boolean,
  labels: VikingBotLabels,
  locale: Locale,
): MentionConfig | undefined {
  if (!config) return undefined;
  return {
    option: [],
    shouldShow: ({ editor }) => !disabled && editor.isFocused,
    onSelect: (item, { editor, range }) => {
      const reference = config.rows.find(
        (row) => row.kind === 'entry' && row.uri === item.value,
      );
      if (
        disabled ||
        !reference ||
        reference.kind !== 'entry' ||
        reference.disabled
      )
        return;
      selectReference(config, reference);
      editor.chain().focus().deleteRange(range).run();
    },
    render: ({ query, command, editor }) =>
      disabled ? null : (
        <ReferencePicker
          locale={locale}
          config={config}
          editor={editor}
          query={query}
          labels={labels}
          onSelect={(item) =>
            command({ tagType: 'file', label: item.name, value: item.uri })
          }
        />
      ),
  };
}

export function ReferenceButton({
  locale,
  config,
  disabled,
  labels,
  senderRef,
}: {
  config: VikingBotReferences;
  disabled?: boolean;
  labels: Required<VikingBotLabels>;
  locale: Locale;
  senderRef: RefObject<SenderInstance | null>;
}) {
  return (
    <BotPopover
      key={disabled ? 'disabled' : 'enabled'}
      label={labels.reference}
      placement="top"
      trigger={(props) => (
        <Tooltip
          content={labels.reference}
          disabled={props['aria-expanded'] === true}
        >
          <Button
            {...props}
            size="mini"
            type="text"
            iconOnly
            className="ov-viking-bot__attach"
            htmlType="button"
            aria-label={labels.reference}
            disabled={disabled}
          >
            @
          </Button>
        </Tooltip>
      )}
    >
      {(close) => (
        <ReferencePicker
          locale={locale}
          config={config}
          labels={labels}
          onSelect={(item) => {
            if (disabled) return;
            selectReference(config, item);
            close();
            senderRef.current?.getEditor()?.commands.focus();
          }}
        />
      )}
    </BotPopover>
  );
}
