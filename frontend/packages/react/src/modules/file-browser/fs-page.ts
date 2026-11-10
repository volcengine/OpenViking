import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FsEntry } from './fs-model.js';

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
