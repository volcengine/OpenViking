import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FsEntry } from './fs-model.js';
import { isSystemUri } from './preview-policy.js';

/** 一次取多少个子节点。One page of children. */
export const PAGE_SIZE = 200;

/**
 * 读一页子节点。单独成文件是因为它是纯请求逻辑：没有状态、没有取消管理，调用方
 * 传入 signal 即可；留在 Hook 里只会让那个文件超出行数上限。
 *
 * Reads one page of children. It lives in its own file because it is pure request
 * logic with no state and no cancellation of its own — the caller supplies the
 * signal — and keeping it in the Hook would only push that file past the line limit.
 */
export async function loadPage(
  client: OvClient,
  uri: string,
  offset: number,
  signal: AbortSignal,
  requestOptions?: OvRequestOptions,
): Promise<{ entries: FsEntry[]; hasMore: boolean }> {
  const envelope = await client.requestEnvelope<FsEntry[]>({
    method: 'GET',
    path: OV_PATHS.fsLs,
    query: {
      uri,
      output: 'agent',
      // `include_tags` 让服务端读向量库里的检索标签。`viking://system` 是 FILE_ONLY
      // 命名空间，写入不进向量库，对它传这个参数会被判为非法，整个列表请求失败而
      // 不是返回空标签。跳过它也不损失能力：编译目标不允许落在 system 下，那里的
      // 条目不可能带看板判定用的 `compile_target` / `has_conflict` 标签。
      //
      // `include_tags` makes the server read retrieval tags from the vector store.
      // `viking://system` is a FILE_ONLY namespace that never writes there, so the
      // parameter is rejected as invalid and the whole listing fails instead of
      // returning no tags. Skipping it loses nothing: compile targets may not live
      // under system, so its entries cannot carry the `compile_target` /
      // `has_conflict` tags the dashboards look for.
      include_tags: isSystemUri(uri) ? undefined : true,
      node_limit: PAGE_SIZE,
      offset,
      // 不传 `show_all_hidden`：默认列表已排除 `.abstract.md` / `.overview.md`
      // 这类内部摘要文件，它们属于目录的两级摘要，由内容区的页签展示，不该作为
      // 普通文件行出现。靠接口的默认过滤，而不是在这里按文件名硬编码排除。
      //
      // `show_all_hidden` is deliberately omitted: the default listing already
      // excludes internal summary files such as `.abstract.md` and `.overview.md`.
      // Those are a directory's two summary levels, shown by the content-area tabs,
      // and must not appear as ordinary file rows. Relying on the API's default
      // filter beats hard-coding file names here.
    },
    options: { ...requestOptions, signal },
  });
  return { entries: envelope.result ?? [], hasMore: envelope.hasMore };
}
