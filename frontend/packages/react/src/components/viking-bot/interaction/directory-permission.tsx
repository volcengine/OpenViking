import type { InteractionLabels } from './labels.js';
import {
  interactionError,
  resolveDirectoryPermission,
} from './directory-state.js';

export function DirectoryPermission({
  permission,
  labels,
}: {
  permission: ReturnType<typeof resolveDirectoryPermission>;
  labels: InteractionLabels;
}) {
  const error = permission.error
    ? interactionError(permission.error, labels)
    : undefined;
  return (
    <p
      className={`ov-viking-bot__directory-status${error ? ' ov-viking-bot__directory-status--error' : ''}`}
      role={error ? 'alert' : 'status'}
    >
      {error ??
        (permission.checking
          ? labels.directoryPermissionChecking
          : permission.info?.canWrite
            ? labels.directoryPermissionWritable
            : permission.info
              ? labels.directoryNotWritable
              : labels.directoryPermissionHint)}
    </p>
  );
}
