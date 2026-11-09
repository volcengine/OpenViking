import { useEffect, useState } from 'react';

import type {
  FilePreviewDirectoryTab,
  FilePreviewFileTab,
  FilePreviewTab,
} from '../../components/file-preview/index.js';
import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { isBinaryFile, mimeTypeOf, toBinaryTab } from './fs-file-kind.js';
import { directoryIcon, fileTypeIcon } from './fs-icon.js';
import { isAbort, nameFromUri, toDirectoryTab, toFileTab } from './fs-model.js';

/** 读取正文的行数上限；读满即按截断处理。 */
const MAX_LINES = 2000;

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
   * 当前资源是否是目录。目录读 L0/L1 摘要，文件读正文。
   * Whether the current resource is a directory. A directory reads its L0/L1
   * summary; a file reads its content.
   */
  isDirectory?: boolean;
  /** 目录两级摘要的标题。Titles of the two summary levels. */
  labels?: { l0: string; l1: string };
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
const DEFAULT_SUMMARY_LABELS = { l0: 'L0 Summary', l1: 'L1 Overview' };

export function useFsPreview({
  client,
  uri,
  isDirectory = false,
  labels = DEFAULT_SUMMARY_LABELS,
  openParent,
  generation = 0,
  requestOptions,
}: UseFsPreviewOptions): PreviewState {
  const [tabs, setTabs] = useState<FilePreviewTab[] | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [footer, setFooter] = useState<string | undefined>(undefined);

  // 当前资源决定要读什么。资源切换必须取消上一次读取，否则慢的旧响应会覆盖新
  // 资源的页签；卸载时同样取消。
  //
  // The current resource decides what is read. A resource switch cancels the
  // previous read, or a slow response would overwrite the new resource's tabs; the
  // same happens on unmount.
  useEffect(() => {
    if (!client || !uri) {
      setTabs(null);
      setActiveKey(null);
      setError(null);
      setFooter(undefined);
      return;
    }
    const controller = new AbortController();
    // 加载中也给出一个页签：外壳（页签行、路径行）因此常驻，只有正文区显示
    // 加载态。直接置 null 会让整个预览区消失再出现，切文件时闪一屏。
    //
    // A tab is present even while loading, so the shell — tab row and path row —
    // stays put and only the body shows the loading state. Setting null would make
    // the whole preview area disappear and reappear, flashing on every switch.
    setTabs([loadingTab(uri, isDirectory)]);
    setActiveKey(uri);
    setActiveKey(null);
    setError(null);
    setFooter(undefined);
    const options = { ...requestOptions, signal: controller.signal };

    const read: Promise<FilePreviewTab> = isDirectory
      ? readDirectory(client, uri, options, labels, openParent)
      : readFile(client, uri, options, openParent);

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
      (tab) => {
        if (controller.signal.aborted) return;
        setTabs([tab]);
        setActiveKey(tab.key);
        // 设计稿底部显示字符数；接口不返回行数与更新时间，先给字符数。
        const body =
          tab.kind === 'directory'
            ? (tab.levels[0].source.content ?? '')
            : tab.kind === 'file'
              ? (tab.source.content ?? '')
              : '';
        setFooter(`${body.length} characters`);
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
    isDirectory,
    labels,
    openParent,
    generation,
    requestOptions,
  ]);

  return { tabs, activeKey, error, footer, setActiveKey };
}

/**
 * 加载中占位页签：只带 key 与文件名，`loading` 让组件渲染加载态而不是「空文件」。
 * A placeholder tab while loading: it carries the key and file name, and `loading`
 * makes the component render its loading state rather than "empty file".
 */
function loadingTab(uri: string, isDirectory: boolean): FilePreviewTab {
  if (isDirectory) {
    return {
      kind: 'directory',
      key: uri,
      loading: true,
      levels: [
        {
          key: 'l0',
          label: '',
          source: {
            fileName: `${nameFromUri(uri)}.md`,
            path: uri,
            fileType: 'markdown',
            content: '',
          },
        },
        {
          key: 'l1',
          label: '',
          source: {
            fileName: `${nameFromUri(uri)}.md`,
            path: uri,
            fileType: 'markdown',
            content: '',
          },
        },
      ],
    };
  }
  return {
    kind: 'file',
    key: uri,
    loading: true,
    source: { fileName: nameFromUri(uri), path: uri, content: '' },
  };
}

/**
 * 读文件并包装成页签。文本走 `content/read`，二进制走 `content/download` 取原始
 * 字节——前者把内容当文本返回，二进制文件经过它只会得到一串乱码，图片与 PDF
 * 因此永远渲染不出来。
 *
 * Reads a file into a tab. Text goes through `content/read` and binary files
 * through `content/download` for the raw bytes: the former returns content as
 * text, so a binary file only yields mojibake and images or PDFs never render.
 */
async function readFile(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
  openParent?: (uri: string) => Promise<void>,
): Promise<FilePreviewFileTab> {
  const name = nameFromUri(uri);
  const icon = fileTypeIcon(name);

  if (isBinaryFile(name)) {
    const blob = await client.request<Blob>({
      method: 'GET',
      path: OV_PATHS.contentDownload,
      query: { uri },
      responseType: 'blob',
      options,
    });
    // 接口返回 octet-stream，这里按扩展名补上真实类型：底座靠它选择 Adapter。
    // The endpoint answers octet-stream; the real type is restored from the
    // extension, which is what the base uses to pick an adapter.
    const typed = new Blob([blob], { type: mimeTypeOf(name) });
    return toBinaryTab(uri, name, typed, icon, openParent);
  }

  const content = await client.request<string>({
    method: 'GET',
    path: OV_PATHS.contentRead,
    query: { uri, limit: MAX_LINES },
    options,
  });
  return toFileTab(
    { uri },
    typeof content === 'string' ? content : '',
    MAX_LINES,
    icon,
    openParent,
  );
}

/**
 * 读目录的两级摘要，各自成一个页签：L0 是摘要，L1 是总览。
 *
 * 两个接口分别请求，一个失败不影响另一个；两个都为空时仍给出两个空页签，
 * 让使用者知道目录存在但没有生成内容，而不是看到一片空白。
 *
 * Reads a directory's two summary levels, one tab each: L0 is the abstract and L1
 * the overview.
 *
 * The two endpoints are requested separately so one failing does not hide the
 * other; when both are empty the tabs still appear, so the user sees that the
 * directory exists but has no generated content instead of blank space.
 */
async function readDirectory(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
  labels: { l0: string; l1: string },
  openParent?: (uri: string) => Promise<void>,
): Promise<FilePreviewDirectoryTab> {
  // 失败不吞：两级摘要各自可能为空（目录还没生成），但请求失败是另一回事，
  // 静默变成空串会让「没生成」和「取不到」看起来一样，用户无从判断。
  //
  // Failures are not swallowed: either level may legitimately be empty (a
  // directory that has not been summarised yet), but a failed request is a
  // different thing. Turning it into an empty string silently makes "not
  // generated" and "could not load" look identical, leaving the user unable to
  // tell which happened.
  // 单级失败记为空，但两级都失败就当作整体失败抛出去：只失败一级通常意味着
  // 那一级还没生成，两级都失败则是连接或权限出了问题，必须让用户看到。
  //
  // One level failing counts as empty, but both failing is thrown: a single
  // failure usually means that level is not generated yet, while both mean a
  // connection or permission problem the user has to see.
  const read = (path: string) =>
    client
      .request<string>({ method: 'GET', path, query: { uri }, options })
      .then((value) => ({
        ok: true as const,
        text: typeof value === 'string' ? value : '',
      }))
      .catch((cause: unknown) => ({ ok: false as const, cause }));

  // 两级摘要都读出来，交给目录页签：L0/L1 是它内部的一层同级切换，不是文件
  // 页签——后者回答「打开了哪些文件」，前者回答「这个目录看哪一级摘要」。
  //
  // Both levels are read and handed to the directory tab: L0/L1 is a level of
  // sibling switching inside it, not file tabs. A file tab answers "which files
  // are open"; these answer "which summary level of this directory is shown".
  const [abstractResult, overviewResult] = await Promise.all([
    read(OV_PATHS.contentAbstract),
    read(OV_PATHS.contentOverview),
  ]);
  // 被取消的那一级不是「没有生成」，而是「这次请求作废了」：把它当作空摘要
  // 返回，调用方就会拿着一个过期结果当成成功。取消优先于其余失败上报。
  //
  // An aborted level is not "not generated" but "this request was cancelled":
  // returning it as an empty summary would hand the caller a stale success. A
  // cancellation therefore outranks any other failure.
  const aborted = [abstractResult, overviewResult].find(
    (result) => !result.ok && isAbort(result.cause),
  );
  if (aborted && !aborted.ok) throw aborted.cause;
  if (!abstractResult.ok && !overviewResult.ok) {
    throw abstractResult.cause;
  }

  return toDirectoryTab(
    uri,
    {
      abstract: abstractResult.ok ? abstractResult.text : '',
      overview: overviewResult.ok ? overviewResult.text : '',
    },
    labels,
    openParent,
    directoryIcon(),
  );
}
