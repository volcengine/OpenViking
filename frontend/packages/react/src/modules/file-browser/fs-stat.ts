import dayjs from 'dayjs';
import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';

interface StatResult {
  isDir?: boolean;
  is_dir?: boolean;
  modTime?: string | number;
  mtime?: string | number;
  updateTime?: string | number;
}

function isoTimestamp(value: string | number | undefined) {
  if (value == null || (typeof value === 'string' && !value.trim())) return;
  const numeric = Number(value);
  if (numeric === 0) return;
  const time = dayjs(
    Number.isFinite(numeric)
      ? numeric < 1e12
        ? numeric * 1000
        : numeric
      : value,
  );
  return time.isValid() ? time.toISOString() : undefined;
}

/**
 * 读取预览的资源类型与更新时间。已知类型时可与正文并行读取。
 * Reads the type and update time. With a known type, content loads in parallel.
 * A metadata failure does not block content.
 */
export async function readPreviewStat(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) {
  try {
    const result = await client.request<StatResult>({
      method: 'GET',
      path: OV_PATHS.fsStat,
      query: { uri },
      options,
    });
    return {
      isDirectory: result?.isDir === true || result?.is_dir === true,
      updatedAt:
        isoTimestamp(result?.modTime) ??
        isoTimestamp(result?.mtime) ??
        isoTimestamp(result?.updateTime),
    };
  } catch {
    return undefined;
  }
}

/** 一个带 `AbortError` 名字的错误，交给既有的取消分支处理。 */
/** An error named `AbortError`, for the existing cancellation branch. */
export function abortError(): Error {
  const error = new Error('Aborted');
  error.name = 'AbortError';
  return error;
}
