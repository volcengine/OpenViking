import { RecordBody } from './tui-record.js';
import { resolveTuiLabels } from './tui-labels.js';
import type { TuiRecord } from './types.js';

/** Result body without command input; hosts can retain task and session controls. */
export function TuiResult({
  record,
  locale = 'en',
  onOpenUri,
  onCopy,
}: {
  record: TuiRecord;
  locale?: 'en' | 'zh';
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
}) {
  return (
    <div className="ov-tui" style={{ height: 'auto', minHeight: 0 }}>
      <RecordBody
        record={record}
        labels={resolveTuiLabels(undefined, locale)}
        onOpenUri={onOpenUri}
        onCopy={onCopy}
      />
    </div>
  );
}
