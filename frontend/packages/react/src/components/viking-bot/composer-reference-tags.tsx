import type { CSSProperties } from 'react';
import {
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from '../../icons/file-type-icon.js';
import { VikingBotIcon } from '../../icons/viking-bot-icons.js';
import { BotPopover } from './bot-popover.js';
import type {
  VikingBotReference,
  VikingBotReferences,
} from './capabilities.js';
import type { VikingBotLabels } from './model.js';

const VISIBLE_LIMIT = 5;

function ReferenceTag({
  item,
  labels,
  disabled,
  onRemove,
  onOpenUri,
}: {
  item: VikingBotReference;
  labels: Required<VikingBotLabels>;
  disabled?: boolean;
  onRemove: () => void;
  onOpenUri?: (uri: string) => void;
}) {
  const FileIcon = getFileTypeIcon(item.name);
  const iconKey = resolveFileTypeIconKey(item.name);
  const path = (
    <span className="ov-viking-bot__reference-path" title={item.uri}>
      {item.uri}
    </span>
  );
  return (
    <span className="ov-viking-bot__reference-tag">
      {item.type === 'directory' ? (
        <VikingBotIcon name="contextFolder" width={14} height={14} />
      ) : (
        <FileIcon
          className="ov-viking-bot__reference-icon"
          width={14}
          height={14}
          aria-hidden="true"
          focusable="false"
          style={
            {
              '--ov-ui-viking-bot-reference-icon-light':
                getFileTypeColor(iconKey),
              '--ov-ui-viking-bot-reference-icon-dark': getFileTypeColor(
                iconKey,
                'dark',
              ),
            } as CSSProperties
          }
        />
      )}
      {onOpenUri ? (
        <button
          type="button"
          className="ov-viking-bot__reference-open"
          aria-label={labels.openReference.replace('{uri}', item.uri)}
          onClick={() => onOpenUri(item.uri)}
        >
          {path}
        </button>
      ) : (
        path
      )}
      <button
        type="button"
        className="ov-viking-bot__reference-remove"
        disabled={disabled}
        aria-label={`${labels.removeReference}: ${item.uri}`}
        onClick={onRemove}
      >
        <VikingBotIcon name="contextClose" width={14} height={14} />
      </button>
    </span>
  );
}

export function ReferenceTags({
  config,
  labels,
  disabled,
  onOpenUri,
}: {
  config?: VikingBotReferences;
  labels: Required<VikingBotLabels>;
  disabled?: boolean;
  onOpenUri?: (uri: string) => void;
}) {
  if (!config?.selected.length) return null;
  const items = config.selected;
  const remaining = items.slice(VISIBLE_LIMIT);
  const renderTag = (item: VikingBotReference, index: number) => (
    <ReferenceTag
      key={`${item.uri}:${index}`}
      item={item}
      labels={labels}
      disabled={disabled}
      onOpenUri={onOpenUri}
      onRemove={() =>
        config.onChange(items.filter((_, position) => position !== index))
      }
    />
  );
  return (
    <div className="ov-viking-bot__reference-tags">
      {items.slice(0, VISIBLE_LIMIT).map((item, index) => (
        <div
          className="ov-viking-bot__reference-row"
          key={`${item.uri}:${index}`}
        >
          {renderTag(item, index)}
          {index === VISIBLE_LIMIT - 1 && remaining.length > 0 ? (
            <BotPopover
              label={labels.reference}
              placement="top"
              hover
              trigger={(props) => (
                <button
                  {...props}
                  type="button"
                  className="ov-viking-bot__reference-more"
                >
                  +{remaining.length}
                </button>
              )}
            >
              {() => (
                <div className="ov-viking-bot__reference-overflow">
                  {remaining.map((entry, position) =>
                    renderTag(entry, position + VISIBLE_LIMIT),
                  )}
                </div>
              )}
            </BotPopover>
          ) : null}
        </div>
      ))}
    </div>
  );
}
