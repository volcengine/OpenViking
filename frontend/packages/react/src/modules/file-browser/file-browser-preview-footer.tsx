import type { FilePreviewTab } from '../../components/file-preview/index.js';
import type { FileBrowserApi } from './file-browser-state-types.js';
import { normalizeResourceUri } from '../../shared/resource-uri.js';
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
  const aiLabel = aiGenerated && text.previewAiGenerated;
  return (
    <>
      {count || time || aiLabel ? (
        <span className="ov-file-browser__preview-meta">
          {count ? <span>{count}</span> : null}
          {count && time ? <span aria-hidden="true">·</span> : null}
          {time ? (
            <time dateTime={updatedAt}>
              {text.formatPreviewUpdatedAt(time)}
            </time>
          ) : null}
          {aiLabel && (count || time) ? (
            <span aria-hidden="true">·</span>
          ) : null}
          {aiLabel ? <span>{aiLabel}</span> : null}
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

/** Match only generated document namespaces; raw uploads stay unlabelled. */
function isAiGeneratedPreview(uri: string | null, isCompile: boolean) {
  if (!uri) return false;
  uri = normalizeResourceUri(uri);
  if (/^viking:\/\/system\/compile\/example-skills(?:\/|$)/i.test(uri))
    return false;
  return (
    isCompile ||
    isSummaryUri(uri) ||
    /^viking:\/\/(?:user\/[^/]+|~)\/SOUL\.md$/.test(uri) ||
    /^viking:\/\/(?:user\/[^/]+|~)(?:\/peers\/[^/]+)?\/memories\/(?:identity|soul)\.md$/i.test(
      uri,
    ) ||
    /^viking:\/\/(?:user\/[^/]+|~)(?:\/peers\/[^/]+)?\/memories\/(?:experiences|preferences|entities|cases|trajectories|events)\/.+/i.test(
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
  const aiGenerated =
    tab?.kind === 'directory'
      ? browser.previewHasDirectorySummary
      : isAiGeneratedPreview(uri, browser.previewIsCompile);
  return tab &&
    tab.kind !== 'content' &&
    !tab.loading &&
    tab.key === uri &&
    (browser.previewFooter ||
      browser.previewUpdatedAt ||
      modelNotice ||
      (aiGenerated && text.previewAiGenerated)) ? (
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
