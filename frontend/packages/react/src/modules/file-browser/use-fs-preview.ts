import { useEffect, useState } from 'react';

import type {
  FilePreviewDirectoryTab,
  FilePreviewTab,
} from '../../components/file-preview/index.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { directoryIcon } from './fs-icon.js';
import { resolveLabels, resolveSummaryLabels } from './file-browser-labels.js';
import { isAbort, toDirectoryTab, type DirectorySummary } from './fs-model.js';
import { isSystemUri } from './preview-policy.js';
import { readDirectory } from './fs-read-directory.js';
import { loadingTab } from './fs-preview-loading-tab.js';
import { toSystemDirectoryTab } from './fs-system-directory-tab.js';
import { createPreviewImageResolver } from './fs-preview-images.js';
import { readFile } from './fs-read-file.js';
import { abortError, resolveIsDirectory } from './fs-stat.js';

export interface PreviewState {
  tabs: readonly FilePreviewTab[] | null;
  activeKey: string | null;
  error: Error | null;
  /** 底部元信息，例如字符数。Footer metadata such as the character count. */
  footer?: string;
  setActiveKey: (key: string) => void;
}

export interface UseFsPreviewOptions {
  client: OvClient | null;
  uri: string | null;
  /**
   * 当前资源的类型。目录读 L0/L1 摘要，文件读正文，`unknown` 先查一次 `fs/stat`
   * 再决定。
   *
   * 传三态而不是「目录集合」：集合每次加载都会换新引用，作为 effect 依赖会让预览
   * 反复重取。三态是稳定的字符串，换引用的问题不存在。
   *
   * The type of the current resource. A directory reads its L0/L1 summary, a file reads
   * its content, and `unknown` resolves with one `fs/stat` call first.
   *
   * A three-state value rather than the directory set: the set gets a new identity on
   * every load, and as an effect dependency it would make the preview reload
   * repeatedly. The three-state value is a stable string, so that cannot happen.
   */
  resourceType?: 'directory' | 'file' | 'unknown';
  contentKind?: 'plain' | 'wiki' | 'pending';
  activeLevelKey?: string;
  onActiveLevelChange?: (key: string) => void;
  /** 目录两级摘要的标题。Titles of the two summary levels. */
  labels?: { l0: string; l1: string; failure: string };
  /** 字符数文案；独立 Hook 默认英文。Character count copy; standalone Hooks default to English. */
  formatCharacterCount?: (count: number) => string;
  /**
   * 打开路径上的某一级祖先。面包屑与目录页签共用它。
   * Opens one ancestor level; the breadcrumb and a directory tab share it.
   */
  openParent?: (uri: string) => Promise<void>;
  /**
   * 重新取数的代次。宿主调用刷新时递增，让同一资源的正文重新请求——内容在服务
   * 端变了而 URI 没变，只靠依赖是看不出来的。
   *
   * Reload generation. A host increments it on refresh so the same resource is read
   * again: the content changed on the server while the URI did not, which
   * dependencies alone cannot express.
   */
  generation?: number;
  requestOptions?: OvRequestOptions;
}

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
  resourceType = 'file',
  contentKind = 'plain',
  activeLevelKey = 'l0',
  onActiveLevelChange,
  labels = resolveSummaryLabels('en-US'),
  formatCharacterCount = resolveLabels(undefined, 'en-US').formatCharacterCount,
  openParent,
  generation = 0,
  requestOptions,
}: UseFsPreviewOptions): PreviewState {
  const [directory, setDirectory] = useState<{
    uri: string;
    summary: DirectorySummary;
    resolveImage: FilePreviewDirectoryTab['resolveImage'];
  } | null>(null);
  const [tabs, setTabs] = useState<FilePreviewTab[] | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [characterCount, setCharacterCount] = useState<number>();
  const [saveGeneration, setSaveGeneration] = useState(0);

  // useEffect 必要性：按 URI、资源类型与请求配置同步远端正文和目录摘要。
  // URI 也会由宿主属性改变，点击事件无法覆盖这些变化，渲染本身不能发请求。
  // 依赖变化与卸载中止请求并忽略过期结果；摘要层级与文案仅影响展示。
  useEffect(() => {
    setDirectory(null);
    if (!client || !uri) {
      setTabs(null);
      setActiveKey(null);
      setError(null);
      setCharacterCount(undefined);
      return;
    }
    const controller = new AbortController();
    // 加载中也给出一个页签：外壳（页签行、路径行）因此常驻，只有正文区显示
    // 加载态。直接置 null 会让整个预览区消失再出现，切文件时闪一屏。
    //
    // A tab is present even while loading, so the shell — tab row and path row —
    // stays put and only the body shows the loading state. Setting null would make
    // the whole preview area disappear and reappear, flashing on every switch.
    setTabs([loadingTab(uri, resourceType !== 'file')]);
    setActiveKey(uri);
    setError(null);
    setCharacterCount(undefined);
    const options = { ...requestOptions, signal: controller.signal };
    if (contentKind === 'pending') return () => controller.abort();

    // 类型未知时先问一次 `fs/stat`。仅凭「不在已知目录集合里」就按文件处理是错的：
    // 集合只覆盖已加载的层，宿主直接传一个尚未展开过的目录 URI 时它就是空的，
    // 于是目录会被读成正文、丢掉 L0/L1 摘要。缺失的是元数据，不是「它是文件」的
    // 证据，因此先查再决定。
    //
    // An unknown type is resolved with one `fs/stat` call first. Treating "not in the
    // known directory set" as a file is wrong: the set only covers loaded levels, so a
    // directory URI the host passes directly, before it was ever expanded, is simply
    // absent — and the directory would be read as content and lose its L0/L1 summary.
    // What is missing is metadata, not evidence that it is a file.
    const read = (async () => {
      const isDir =
        resourceType === 'unknown'
          ? await resolveIsDirectory(client, uri, options)
          : resourceType === 'directory';
      if (controller.signal.aborted) throw abortError();
      if (isDir)
        return isSystemUri(uri)
          ? { tab: toSystemDirectoryTab(uri, openParent) }
          : { summary: await readDirectory(client, uri, options) };
      return {
        tab: await readFile(
          client,
          uri,
          options,
          openParent,
          () => {
            if (!controller.signal.aborted)
              setSaveGeneration((value) => value + 1);
          },
          contentKind === 'wiki',
        ),
      };
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
        if (controller.signal.aborted) return;
        if (result.summary) {
          setDirectory({
            uri,
            summary: result.summary,
            resolveImage: createPreviewImageResolver(
              client,
              uri,
              true,
              options,
            ),
          });
          setActiveKey(uri);
          return;
        }
        const tab = result.tab;
        setTabs([tab]);
        setActiveKey(tab.key);
        // 设计稿底部显示字符数；接口不返回行数与更新时间，先给字符数。
        const body = tab.kind === 'file' ? tab.source.content : undefined;
        setCharacterCount(typeof body === 'string' ? body.length : undefined);
      },
      (cause: unknown) => {
        if (isAbort(cause) || controller.signal.aborted) return;
        setTabs([]);
        setError(cause instanceof Error ? cause : new Error(String(cause)));
      },
    );

    return () => controller.abort();
  }, [
    client,
    uri,
    resourceType,
    contentKind,
    openParent,
    generation,
    saveGeneration,
    requestOptions,
  ]);

  const directoryTab =
    directory?.uri === uri
      ? {
          ...toDirectoryTab(
            directory.uri,
            directory.summary,
            labels,
            openParent,
            directoryIcon(),
          ),
          resolveImage: directory.resolveImage,
          activeLevelKey,
          onActiveLevelChange,
        }
      : undefined;
  const count = directoryTab
    ? (
        directoryTab.levels.find((level) => level.key === activeLevelKey)
          ?.source.content ?? ''
      ).length
    : characterCount;
  return {
    tabs: directoryTab ? [directoryTab] : tabs,
    activeKey,
    error,
    footer: count === undefined ? undefined : formatCharacterCount(count),
    setActiveKey,
  };
}
