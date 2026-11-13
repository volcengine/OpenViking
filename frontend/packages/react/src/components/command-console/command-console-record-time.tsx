import { useSyncExternalStore } from 'react';
import type { ResolvedCommandConsoleLabels } from './command-console-labels.js';
import type { CommandConsoleRecord } from './types.js';

type RecordTime = Pick<CommandConsoleRecord, 'createdAt' | 'createdAtMs'>;

/** 按实际经过的毫秒计算相对时间；展示字符串不参与日期解析。 */
export function formatCommandConsoleRecordTime(
  record: RecordTime,
  labels: ResolvedCommandConsoleLabels,
  now = Date.now(),
): string | undefined {
  const timestamp = record.createdAtMs;
  if (typeof timestamp !== 'number' || !Number.isFinite(timestamp))
    return record.createdAt;
  const date = new Date(timestamp);
  if (!Number.isFinite(date.getTime())) return record.createdAt;
  const elapsed = Math.max(0, now - timestamp);
  if (elapsed < 60_000) return labels.justNow;
  if (elapsed < 3_600_000)
    return labels.formatMinutesAgo(Math.floor(elapsed / 60_000));
  if (elapsed < 86_400_000)
    return labels.formatHoursAgo(Math.floor(elapsed / 3_600_000));
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

const subscribe = () => () => {};
const clientSnapshot = () => true;
const serverSnapshot = () => false;

/** 客户端渲染时读取时钟，SSR 与 hydration 首屏使用相同的兜底文案。 */
export function CommandConsoleRecordTime({
  record,
  labels,
}: {
  record: RecordTime;
  labels: ResolvedCommandConsoleLabels;
}) {
  const client = useSyncExternalStore(
    subscribe,
    clientSnapshot,
    serverSnapshot,
  );
  const text = client
    ? formatCommandConsoleRecordTime(record, labels)
    : record.createdAt;
  return text ? (
    <span className="ov-command-console__bubble-time">{text}</span>
  ) : null;
}
