import { useCallback, useState } from 'react';
import type { FilePreviewTab } from '../../components/file-preview/index.js';
import { parseSummaryUri } from './fs-summary-uri.js';
import { normalizeResourceUri } from './fs-uri.js';
import type { SummaryLevel } from './fs-summary-uri.js';

/**
 * 记录由摘要链接指定的层级，并把它交给对应的目录页签。层级通过 FilePreview 的
 * 受控入口 `activeLevelKey` 传入，用户之后手动切换时同步更新，不靠 DOM 操作。
 *
 * Remembers the level a summary link asked for and hands it to the matching
 * directory tab through FilePreview's controlled `activeLevelKey`. Manual switches
 * afterwards update it too, so nothing depends on the DOM.
 */
export function useSummaryLevel() {
  const [target, setTarget] = useState<{ uri: string; key: SummaryLevel }>();

  /**
   * 解析一次导航：返回实际要打开的 URI，并记下或清除层级。每次导航都要走这里，
   * 否则上一次摘要链接的层级会在之后重新打开同一目录时再次生效。
   *
   * Resolves one navigation: returns the URI to actually open and records or
   * clears the level. Every navigation goes through here, or a previous summary
   * link's level would apply again when the same directory is reopened later.
   */
  const resolve = useCallback((uri: string) => {
    const summary = parseSummaryUri(uri);
    setTarget(
      summary ? { uri: summary.directory, key: summary.level } : undefined,
    );
    return summary?.directory ?? uri;
  }, []);

  const decorate = useCallback(
    (tabs: readonly FilePreviewTab[] | null) =>
      target && tabs
        ? tabs.map((tab) =>
            tab.kind === 'directory' &&
            // 宿主可能把 URI 规范化（比如去掉末尾斜杠）后再传回来。
            // The host may normalise the URI (e.g. drop a trailing slash) before passing it back.
            normalizeResourceUri(tab.key) === normalizeResourceUri(target.uri)
              ? {
                  ...tab,
                  activeLevelKey: target.key,
                  onActiveLevelChange: (key: string) =>
                    setTarget({ uri: target.uri, key: key as SummaryLevel }),
                }
              : tab,
          )
        : tabs,
    [target],
  );

  return { resolve, decorate };
}
