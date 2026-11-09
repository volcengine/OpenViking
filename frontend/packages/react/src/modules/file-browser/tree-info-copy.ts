import type { FileTreeLabels } from '../../components/file-tree/index.js';
import { TREE_INFO_LABELS_ZH_CN } from './tree-info-copy.zh-CN.js';

/**
 * 按语言解析目录树的文案。组件内置的是英文，模块这一层必须整份给中文——只
 * 覆盖其中几项会让同一张卡片里中英文混排，比全英文更难读。
 *
 * 宿主的 `info` 逐键叠在内置值之上：整份替换会丢掉宿主没提到的键。
 *
 * Resolves the tree copy for a language. The component ships English, so the module
 * supplies Chinese as a whole set — a partial override would mix languages inside
 * one card, which reads worse than English alone.
 *
 * A host `info` layers key by key over the built-in values: replacing it wholesale
 * would drop the keys the host did not mention.
 */
export function resolveTreeLabels(
  locale: 'en-US' | 'zh-CN' | undefined,
  hostLabels: FileTreeLabels | undefined,
): FileTreeLabels | undefined {
  if (locale !== 'zh-CN') return hostLabels;

  const hostInfo = hostLabels?.info;
  return {
    ...hostLabels,
    info: {
      ...TREE_INFO_LABELS_ZH_CN,
      ...hostInfo,
      description: {
        ...TREE_INFO_LABELS_ZH_CN.description,
        ...hostInfo?.description,
      },
      triggerBehavior: {
        ...TREE_INFO_LABELS_ZH_CN.triggerBehavior,
        ...hostInfo?.triggerBehavior,
      },
      field: { ...TREE_INFO_LABELS_ZH_CN.field, ...hostInfo?.field },
    },
  };
}
