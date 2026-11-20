import type { FilePreviewTab } from '../../components/file-preview/index.js';
import type { FileBrowserApi } from './file-browser-state-types.js';
import { isSummaryUri } from './preview-policy.js';
import type { Locale } from '../../shared/locale.js';
import type { ResolvedLabels } from './file-browser-labels.js';

const modelResource =
  /^viking:\/\/(?:resources?|(?:users?\/[^/]+|~)\/resources?)(?:\/|$)/i;

export function FileBrowserPreviewFooter({
  count,
  updatedAt,
  modelNotice,
  aiGenerated = false,
  locale,
  text,
}: {
  count?: string;
  updatedAt?: string;
  modelNotice?: string;
  aiGenerated?: boolean;
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
      {count || time || aiGenerated ? (
        <span className="ov-file-browser__preview-meta">
          {count ? <span>{count}</span> : null}
          {count && time ? <span aria-hidden="true">·</span> : null}
          {time ? (
            <time dateTime={updatedAt}>
              {text.formatPreviewUpdatedAt(time)}
            </time>
          ) : null}
          {aiGenerated && (count || time) ? (
            <span aria-hidden="true">·</span>
          ) : null}
          {aiGenerated ? <span>{text.previewAiGenerated}</span> : null}
        </span>
      ) : null}
      {modelNotice ? (
        <span className="ov-file-browser__preview-model">
          {modelNotice.split(/(\s+)/).map((part, index) => (
            <span key={index} className="ov-file-browser__preview-model-name">
              {part}
            </span>
          ))}
        </span>
      ) : null}
    </>
  );
}

export function previewModelNotice(uri: string | null, label: string) {
  return uri !== null && modelResource.test(uri) ? label : undefined;
}

/** Match only generated document namespaces; raw uploads stay unlabelled. */
export function isAiGeneratedPreview(
  uri: string | null,
  isDirectory: boolean,
  isCompile: boolean,
) {
  if (!uri) return false;
  return (
    isDirectory ||
    isCompile ||
    isSummaryUri(uri) ||
    /^viking:\/\/(?:user\/[^/]+|~)(?:\/peers\/[^/]+)?\/(?:identity|soul)\.md$/i.test(
      uri,
    ) ||
    /^viking:\/\/(?:user\/[^/]+|~)(?:\/peers\/[^/]+)?\/memories\/(?:experiences|preferences|entities|cases|trajectory|events)\/.+/i.test(
      uri,
    ) ||
    /^viking:\/\/(?:system|user\/[^/]+|~)(?:\/peers\/[^/]+)?\/compile\/.+/i.test(
      uri,
    )
  );
}

export function renderPreviewFooter(
  browser: FileBrowserApi,
  tab: FilePreviewTab | undefined,
  locale: Locale,
  text: ResolvedLabels,
) {
  const uri = browser.currentUri;
  const modelNotice = previewModelNotice(uri, text.previewModelNotice);
  const aiGenerated = isAiGeneratedPreview(
    uri,
    tab?.kind === 'directory',
    browser.previewDashboard?.kind === 'wiki',
  );
  return tab &&
    tab.kind !== 'content' &&
    !tab.loading &&
    tab.key === uri &&
    (browser.previewFooter ||
      browser.previewUpdatedAt ||
      modelNotice ||
      aiGenerated) ? (
    <FileBrowserPreviewFooter
      count={browser.previewFooter}
      updatedAt={browser.previewUpdatedAt}
      modelNotice={modelNotice}
      aiGenerated={aiGenerated}
      locale={locale}
      text={text}
    />
  ) : undefined;
}
