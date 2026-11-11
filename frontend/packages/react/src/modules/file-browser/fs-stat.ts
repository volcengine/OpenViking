import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';

/** `fs/stat` 里与类型判定相关的字段。The `fs/stat` fields relevant to the type. */
interface StatResult {
  isDir?: boolean;
  is_dir?: boolean;
}

/**
 * 问服务端一个 URI 是不是目录。
 *
 * 只在类型未知时调用：树只认识已加载的层，宿主直接传一个尚未展开过的目录 URI 时
 * 它不在已知集合里，仅凭这一点按文件处理会让目录丢掉 L0/L1 摘要、改去读正文。
 *
 * 查不到（404、无权限）时按文件处理：调用方本来就要读这个资源，让那次读取去报
 * 真正的失败比在这里拦下来更直接。
 *
 * Asks the server whether a URI is a directory.
 *
 * Only called when the type is unknown: the tree only knows the levels it has loaded,
 * so a directory URI the host passes directly, before it was ever expanded, is absent
 * from the known set — and treating that as a file would make the directory lose its
 * L0/L1 summary and read content instead.
 *
 * A lookup failure (404, no permission) falls back to "file": the caller is about to
 * read the resource anyway, and letting that read report the real failure is more
 * direct than stopping here.
 */
export async function resolveIsDirectory(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
): Promise<boolean> {
  try {
    const result = await client.request<StatResult>({
      method: 'GET',
      path: OV_PATHS.fsStat,
      query: { uri },
      options,
    });
    return result?.isDir === true || result?.is_dir === true;
  } catch {
    return false;
  }
}

/** 一个带 `AbortError` 名字的错误，交给既有的取消分支处理。 */
/** An error named `AbortError`, for the existing cancellation branch. */
export function abortError(): Error {
  const error = new Error('Aborted');
  error.name = 'AbortError';
  return error;
}
