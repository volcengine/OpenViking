import { Button } from '../../shared/primitives/button/button.js';
import SessionCopy from '../../../../icons/svg/viking-bot/session-copy.svg?react';
import SessionCopied from '../../../../icons/svg/viking-bot/session-copied.svg?react';
import SessionFile from '../../../../icons/svg/viking-bot/session-file.svg?react';
import { CopyAction } from './copy-action.js';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import type { VikingBotLabels } from './model.js';

export function SessionMeta({
  id,
  uri,
  labels,
  onCopy,
  onOpenUri,
}: {
  id: string;
  uri?: string;
  labels: Required<VikingBotLabels>;
  onCopy?: (text: string) => void;
  onOpenUri?: (uri: string) => void;
}) {
  const compact = id.length > 22 ? id.slice(0, 10) + '...' + id.slice(-6) : id;
  return (
    <div className="ov-viking-bot__session-meta">
      <span>{labels.session}</span>
      <code title={id}>{compact}</code>
      <div className="ov-viking-bot__session-meta-actions">
        <CopyAction
          text={id}
          labels={labels}
          className="ov-viking-bot__session-meta-action"
          onCopy={onCopy}
          icon={(copied) => {
            const Icon = copied ? SessionCopied : SessionCopy;
            return <Icon aria-hidden="true" focusable="false" />;
          }}
        />
        {uri && onOpenUri ? (
          <Tooltip content={labels.locateSession}>
            <Button
              size="mini"
              type="text"
              iconOnly
              htmlType="button"
              className="ov-viking-bot__session-meta-action"
              aria-label={labels.locateSession}
              onClick={() => onOpenUri(uri)}
            >
              <SessionFile aria-hidden="true" focusable="false" />
            </Button>
          </Tooltip>
        ) : null}
      </div>
    </div>
  );
}
