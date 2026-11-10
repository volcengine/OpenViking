import type { Locale } from '../../shared/locale.js';
import { RecordBody } from './tui-record.js';
import { resolveTuiLabels } from './tui-labels.js';
import type { TuiRecord } from './types.js';

/** Result body without command input; hosts can retain task and session controls. */
export function TuiResult({
  record,
  locale = 'en-US',
  onOpenUri,
  onCopy,
}: {
  record: TuiRecord;
  locale?: Locale;
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
