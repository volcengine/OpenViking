import type { Locale } from '../../shared/locale.js';
import type { ResolvedLabels } from './file-browser-labels.js';

const modelResource =
  /^viking:\/\/(?:resources?|(?:users?\/[^/]+|~)\/resources?)(?:\/|$)/i;

export function FileBrowserPreviewFooter({
  count,
  updatedAt,
  modelNotice,
  locale,
  text,
}: {
  count?: string;
  updatedAt?: string;
  modelNotice?: string;
  locale: Locale;
  text: ResolvedLabels;
}) {
  let time: string | undefined;
  if (updatedAt) {
    const formatter = new Intl.DateTimeFormat(locale, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    });
    const date = new Date(updatedAt);
    if (locale === 'zh-CN') {
      const parts = Object.fromEntries(
        formatter.formatToParts(date).map(({ type, value }) => [type, value]),
      );
      time = `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
    } else {
      time = formatter.format(date);
    }
  }
  return (
    <>
      {count || time ? (
        <span className="ov-file-browser__preview-meta">
          {count ? <span>{count}</span> : null}
          {count && time ? <span aria-hidden="true">·</span> : null}
          {time ? (
            <time dateTime={updatedAt}>
              {text.formatPreviewUpdatedAt(time)}
            </time>
          ) : null}
        </span>
      ) : null}
      {modelNotice ? (
        <span className="ov-file-browser__preview-model">{modelNotice}</span>
      ) : null}
    </>
  );
}

export function previewModelNotice(uri: string | null, label: string) {
  return uri !== null && modelResource.test(uri) ? label : undefined;
}
