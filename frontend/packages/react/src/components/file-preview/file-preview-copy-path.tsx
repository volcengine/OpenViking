import { useExposure } from '../../shared/analytics/use-exposure.js';
import {
  PathCopyButton,
  type PathCopyButtonProps,
} from '../../shared/path-copy-button/path-copy-button.js';

/** File and directory previews share the same path action and exposure event. */
export function FilePreviewCopyPath(props: PathCopyButtonProps) {
  const exposure = useExposure('file_preview_copy_path_show');
  return (
    <span className="ov-file-preview__copy-path">
      <PathCopyButton {...props} buttonRef={exposure} />
    </span>
  );
}
