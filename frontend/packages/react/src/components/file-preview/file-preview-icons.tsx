import { getFileTypeIcon } from '../../icons/index.js';

/** 页签行图标的渲染尺寸。Rendered size of a tab-row icon. */
const ICON_SIZE = 14;

/**
 * 按文件名选择页签图标，复用包的图标集。未命中规则时由图标集回退到通用
 * 文件图标，因此这里不需要自己的兜底映射。
 *
 * Picks the tab icon from the file name using the package icon set. The set falls
 * back to the generic file icon when nothing matches, so no local fallback map is
 * needed here.
 */
export function FileTypeIcon({ fileName }: { fileName: string }) {
  const Icon = getFileTypeIcon(fileName);
  return <Icon width={ICON_SIZE} height={ICON_SIZE} aria-hidden="true" />;
}

/**
 * 内容页签的内置图标，用于宿主未提供 `icon` 的场景。内容页签没有文件名，
 * 用通用文件图标表达「由宿主渲染的内容」。
 *
 * The built-in icon of a content tab, used when the host supplies no `icon`. A
 * content tab has no file name, so the generic file icon stands for
 * "host-rendered content".
 */
export function ContentTabIcon() {
  const Icon = getFileTypeIcon('');
  return <Icon width={ICON_SIZE} height={ICON_SIZE} aria-hidden="true" />;
}
