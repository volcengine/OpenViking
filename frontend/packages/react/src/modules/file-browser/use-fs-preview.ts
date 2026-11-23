import {
  beginWorkflow,
  type Workflow,
} from '../../shared/monitoring/workflow.js';
import { useEffect, useState } from 'react';

import type {
  FilePreviewDirectoryTab,
  FilePreviewTab,
} from '../../components/file-preview/index.js';
import { resolveLabels, resolveSummaryLabels } from './file-browser-labels.js';
import { isAbort } from '../../shared/request/fs-errors.js';
import { toDirectoryTab, type DirectorySummary } from './fs-model.js';
import { isSystemUri } from '../../shared/resource-uri.js';
import { readDirectory } from './fs-read-directory.js';
import { loadingTab } from './fs-preview-loading-tab.js';
import { toSystemDirectoryTab } from './fs-system-directory-tab.js';
import { createPreviewImageResolver as previewImages } from './fs-preview-images.js';
import { readFile } from './fs-read-file.js';
import { loadVideoDetails } from './fs-video-details.js';
import { abortError, toPreviewError } from './fs-stat.js';
import { useWikiSaveFeedback } from './use-wiki-save-feedback.js';
import type { UseFsPreviewOptions } from './preview-types.js';
import { usePreviewLookup } from './use-preview-lookup.js';

/**
 * 当前资源的预览页签。切换资源时旧请求被取消，已产出的页签不会覆盖新资源。
 *
 * 文件读正文，目录读概览（为空时回落到摘要）——两者都是 Markdown，交给同一个
 * 预览组件渲染。
 *
 * Preview tabs of the current resource. A stale request is cancelled when the
 * resource changes, so its tabs never overwrite the new one.
 *
 * A file reads its content and a directory reads its overview, falling back to the
 * abstract; both are Markdown and go through the same preview component.
 */
export function useFsPreview({
  client,
  uri,
  resourceType: treeType = 'file',
  listedUpdatedAt,
  readDashboardHint,
  activeLevelKey = 'l0',
  onActiveLevelChange,
  labels = resolveSummaryLabels('en-US'),
  formatCharacterCount = resolveLabels(undefined, 'en-US').formatCharacterCount,
  openParent,
  generation = 0,
  requestOptions,
}: UseFsPreviewOptions) {
  const [monitoring, setMonitoring] =
    useState<Workflow<'file_browser_preview_load'>>();
  const [directory, setDirectory] = useState<{
    uri: string;
    summary: DirectorySummary;
    resolveImage: FilePreviewDirectoryTab['resolveImage'];
  } | null>(null);
  const [tabs, setTabs] = useState<
    (FilePreviewTab & { compileOutput?: boolean })[] | null
  >(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [stat, setStat] = useState<{ uri: string; updatedAt: string }>();
  const [saveGeneration, setSaveGeneration] = useState(0);
  const [wikiSaveFeedback, wikiSave] = useWikiSaveFeedback(
    client,
    uri,
    requestOptions,
  );
  const lookup = usePreviewLookup({
    client,
    uri,
    requestOptions,
    generation,
    saveGeneration,
    wikiSave,
    listedUpdatedAt,
  });
  const [statType, setStatType] = useState<{
    lookup: typeof lookup;
    type: 'directory' | 'file';
  }>();
  // A late tree listing that confirms fs/stat should keep the original request
  // running. A conflicting type or a new lookup still starts a fresh preview.
  const resourceType =
    statType?.lookup === lookup && statType.type === treeType
      ? 'unknown'
      : treeType;

  // useEffect 必要性：按 URI、资源类型与请求配置同步远端正文和目录摘要。
  // URI 也会由宿主属性改变，点击事件无法覆盖这些变化，渲染本身不能发请求。
  // 依赖变化与卸载中止请求并忽略过期结果。
  useEffect(() => {
    const { client, uri, wikiSave } = lookup;
    if (resourceType !== 'unknown') setStatType(undefined);
    setDirectory(null);
    setStat(undefined);
    wikiSave.reset();
    setActiveKey(client ? uri : null);
    setError(null);
    if (!client || !uri) {
      setTabs(null);
      return;
    }
    const controller = new AbortController();
    // 加载中也给出一个页签：外壳（页签行、路径行）因此常驻，只有正文区显示
    // 加载态。直接置 null 会让整个预览区消失再出现，切文件时闪一屏。
    //
    // A tab is present even while loading, so the shell — tab row and path row —
    // stays put and only the body shows the loading state. Setting null would make
    // the whole preview area disappear and reappear, flashing on every switch.
    setTabs([loadingTab(uri, resourceType)]);
    const { options, onStatus } = wikiSave.bind(controller.signal);
    const { signal } = options;
    const operation = beginWorkflow(
      client,
      'file_browser_preview_load',
      signal,
    );
    if (operation) operation.identity = uri;
    setMonitoring(operation);
    const api = operation?.client(client) ?? client;
    const { metadata, bodyCache } = lookup.prepareFileRead(
      api,
      uri,
      options,
      resourceType === 'unknown',
    );
    void metadata?.then((result) => {
      if (!signal.aborted && result?.updatedAt)
        setStat({ uri, updatedAt: result.updatedAt });
    });

    // 类型未知时先问一次 `fs/stat`。仅凭「不在已知目录集合里」就按文件处理是错的：
    // 集合只覆盖已加载的层，宿主直接传一个尚未展开过的目录 URI 时它就是空的，
    // 于是目录会被读成正文、丢掉 L0/L1 摘要。缺失的是元数据，不是「它是文件」的
    // 证据，因此先查再决定。
    // An unknown type is resolved with one `fs/stat` call first. Treating "not in the
    // known directory set" as a file is wrong: the set only covers loaded levels, so a
    // directory URI the host passes directly, before it was ever expanded, is simply
    // absent — and the directory would be read as content and lose its L0/L1 summary.
    // What is missing is metadata, not evidence that it is a file.
    const read = (async () => {
      const stat = resourceType === 'unknown' ? await metadata : undefined;
      const isDir = stat?.isDirectory ?? resourceType === 'directory';
      if (signal.aborted) throw abortError();
      if (resourceType === 'unknown' && isDir !== undefined) {
        const type = isDir ? 'directory' : 'file';
        setStatType({ lookup, type });
        setTabs([loadingTab(uri, type)]);
      }
      if (isDir)
        return isSystemUri(uri)
          ? { tab: toSystemDirectoryTab(uri, openParent) }
          : { summary: await readDirectory(api, uri, options) };
      return readFile(
        api,
        uri,
        options,
        openParent,
        () => {
          if (!signal.aborted) setSaveGeneration((value) => value + 1);
        },
        onStatus,
        readDashboardHint,
        bodyCache ?? lookup.bodyCacheFor(uri, stat?.updatedAt),
      );
    })();

    // 成功也要复查是否已被取消：目录的两级摘要里，一级先回来、另一级还在路上
    // 时切换资源，`readDirectory` 会把被中止的那一级当成「没有生成」而正常返回，
    // 于是一个已经过期的目录页签会盖住新选中的资源。取消是调用方的意图，任何
    // 结果都不该在它之后落地。
    //
    // Successes are re-checked for cancellation too: with a directory's two summary
    // levels, one may resolve while the other is still in flight, and switching
    // resources then makes `readDirectory` treat the aborted level as "not
    // generated" and resolve successfully — so an obsolete directory tab would
    // overwrite the newly selected resource. Cancellation is the caller's intent and
    // no result may land after it.
    void read.then(
      (result) => {
        if (signal.aborted) return;
        if ('summary' in result && result.summary) {
          setDirectory({
            uri,
            summary: result.summary,
            resolveImage: previewImages(client, uri, true, options),
          });
          return;
        }
        const tab = result.tab;
        setTabs([tab]);
        if ('resolveWikiTab' in result)
          void result.resolveWikiTab?.().then(
            (updatedTab) => {
              if (!signal.aborted && updatedTab) setTabs([updatedTab]);
            },
            (cause: unknown) => {
              if (!signal.aborted) setError(toPreviewError(cause));
            },
          );
        void loadVideoDetails(client, tab, options, (updatedTab) =>
          setTabs([updatedTab]),
        ).catch(() => undefined);
      },
      (cause: unknown) => {
        if (isAbort(cause) || signal.aborted) return;
        operation?.fail(cause, 'protocol', signal);
        setTabs([]);
        setError(toPreviewError(cause));
      },
    );

    return () => controller.abort();
  }, [lookup, resourceType, readDashboardHint, openParent]);

  const directoryTab =
    directory?.uri === uri
      ? toDirectoryTab(directory.uri, directory.summary, labels, openParent, {
          resolveImage: directory.resolveImage,
          activeLevelKey,
          onActiveLevelChange,
        })
      : undefined;
  const current = tabs?.[0];
  const level = directoryTab?.levels.find(
    (item) => item.key === activeLevelKey,
  );
  const count = directoryTab
    ? (level?.source.content ?? '').length
    : current?.key === uri && current.kind === 'file' && !current.loading
      ? current.source.content?.length
      : undefined;
  return {
    monitoring,
    tabs: directoryTab ? [directoryTab] : tabs,
    activeKey,
    error,
    wikiSaveFeedback,
    hasDirectorySummary: Boolean(level?.hasSummary),
    isCompileOutput: current?.key === uri && Boolean(current.compileOutput),
    footer: count === undefined ? undefined : formatCharacterCount(count),
    updatedAt: stat?.uri === uri ? stat.updatedAt : listedUpdatedAt,
    setActiveKey,
  };
}
