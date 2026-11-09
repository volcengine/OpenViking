import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewDirectoryTab } from '../../components/file-preview/index.js';
import { directoryIcon } from './fs-icon.js';
import { isAbort, toDirectoryTab } from './fs-model.js';

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
export async function readDirectory(
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
  // 单级失败不能记成空正文：空正文意味着「还没生成」，而请求失败意味着「取不到」
  // ——后端出错、权限不足都会这样，用户与宿主都要能把两者分开。因此失败的一级
  // 保留失败原因，交给页签显示；两级都失败则整体抛出，让预览显示失败态与重试。
  //
  // A single-level failure must not become an empty body: an empty body means "not
  // generated yet" while a failed request means "could not load" — a backend error or
  // missing permission both look like this, and the user and the host have to tell
  // them apart. The failed level therefore keeps its reason for the tab to show; when
  // both levels fail the whole read is thrown so the preview shows its failure state
  // and a retry.
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

  const reason = (result: { ok: boolean; cause?: unknown }) =>
    result.ok
      ? undefined
      : result.cause instanceof Error
        ? result.cause.message
        : String(result.cause);

  return toDirectoryTab(
    uri,
    {
      abstract: abstractResult.ok ? abstractResult.text : '',
      overview: overviewResult.ok ? overviewResult.text : '',
      abstractError: reason(abstractResult),
      overviewError: reason(overviewResult),
    },
    labels,
    openParent,
    directoryIcon(),
  );
}
