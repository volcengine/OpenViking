import { Button } from '../../shared/primitives/button/button.js';
import type { Locale } from '../../shared/locale.js';
import { createContext, useContext } from 'react';
import type { RefObject } from 'react';
import type { SenderInstance } from '@ark-design/ui/sender';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';
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

export const ComposerReferenceContext = createContext<{
  config?: VikingBotReferences;
  disabled: boolean;
  labels: VikingBotLabels;
  locale: Locale;
} | null>(null);

function MentionReferencePicker({
  editor,
  query,
  command,
}: Parameters<NonNullable<MentionConfig['render']>>[0]) {
  const context = useContext(ComposerReferenceContext);
  if (!context?.config || context.disabled) return null;
  return (
    <ReferencePicker
      locale={context.locale}
      config={context.config}
      editor={editor}
      query={query}
      labels={context.labels}
      onSelect={(item) =>
        command({ tagType: 'file', label: item.name, value: item.uri })
      }
    />
  );
}

export function composerMention(
  config: VikingBotReferences | undefined,
  disabled: boolean,
): MentionConfig {
  return {
    option: [],
    shouldShow: ({ editor }) =>
      Boolean(config && !disabled && editor.isFocused),
    onSelect: (item, { editor, range }) => {
      if (disabled || !config) return;
      const reference = config.rows.find(
        (row) => row.kind === 'entry' && row.uri === item.value,
      );
      if (
        !reference ||
        reference.kind !== 'entry' ||
        reference.disabled ||
        reference.selectable === false
      )
        return;
      selectReference(config, reference);
      editor.chain().focus().deleteRange(range).run();
    },
    // Ark captures this renderer when the mention opens. The React portal
    // still receives later reference rows, loading state and labels via context.
    render: (props) => <MentionReferencePicker {...props} />,
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
      alignment="start"
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
            <span className="ov-viking-bot__attach-glyph" aria-hidden="true">
              @
            </span>
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
