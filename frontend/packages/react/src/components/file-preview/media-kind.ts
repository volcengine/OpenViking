import type { FilePreviewSource } from './types.js';

/**
 * 一份来源是不是图片或音视频，按 MIME 与扩展名判断。
 *
 * 这条规则有两个消费者：正文的渲染分支，以及过渡层判断「要不要异步加载」。放在
 * 这里而不是 `file-preview-media.tsx`，是为了让它不依赖 React 与按钮组件——过渡
 * 判断与测试都能直接导入，新增格式时也只有这一处要改。
 *
 * Whether a source is an image or media, by MIME and extension.
 *
 * Two consumers share this rule: the body's render branch, and the transition's "does
 * this need async loading" test. It lives here rather than in `file-preview-media.tsx`
 * so it carries no React or button dependency — both the transition and the tests can
 * import it directly, and a new format only has to be added once.
 */
export function mediaKind(
  source: FilePreviewSource,
): 'image' | 'video' | undefined {
  const type =
    source.fileType?.toLowerCase() || source.file?.type.toLowerCase() || '';
  if (type === 'image' || type.startsWith('image/')) return 'image';
  if (type === 'video' || type.startsWith('video/')) return 'video';
  const extension = (
    source.fileType ||
    source.fileName.split('.').pop() ||
    ''
  ).toLowerCase();
  if (/^(?:png|jpe?g|gif|webp|avif|bmp|ico|svg)$/.test(extension))
    return 'image';
  if (/^(?:mp4|webm|mov)$/.test(extension)) return 'video';
  return undefined;
}

/**
 * 一份内容是否需要异步加载。空文件、图片与音视频由各自的渲染分支同步完成，
 * 直接换即可；其余交给底座，它会先走一遍 `resolving`。
 *
 * 与 `mediaKind` 同住一个模块，是为了让「怎么分类」和「据此要不要异步加载」始终
 * 由同一条规则决定：两处分开写，新增格式时很容易只改一处。
 *
 * Whether a piece of content loads asynchronously. Empty files, images and media
 * finish synchronously in their own branches and swap directly; everything else goes
 * through the base, which starts with a `resolving` pass.
 *
 * It shares a module with `mediaKind` so that "how content is classified" and "does
 * that mean an async load" always come from one rule; written apart, a new format is
 * easily added to only one of them.
 */
export function needsAsyncLoad(source: FilePreviewSource): boolean {
  return source.content !== '' && !mediaKind(source);
}
