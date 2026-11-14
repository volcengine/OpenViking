import type { InteractionLabels } from './labels.js';
import type { VikingBotInteractionDirectoryPermission } from './directory-picker-types.js';

export class DirectoryFlowError extends Error {
  constructor(
    public code:
      'directoryUnavailable' | 'directoryNotWritable' | 'directoryNameInvalid',
  ) {
    super(code);
  }
}
export function directoryError(
  cause: unknown,
  labels: InteractionLabels,
  fallback: keyof InteractionLabels = 'directoryCheckFailed',
) {
  return cause instanceof DirectoryFlowError
    ? labels[cause.code]
    : cause instanceof Error
      ? cause.message
      : typeof cause === 'string'
        ? cause
        : String(labels[fallback]);
}
export function resolveDirectoryPermission(
  uri: string,
  permission?: VikingBotInteractionDirectoryPermission,
) {
  return permission?.uri === uri
    ? permission
    : { uri, checking: Boolean(uri && permission) };
}
export function childDirectoryUri(parent: string, name: string) {
  const controls = (text: string) =>
    [...text].some(
      (char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127,
    );
  const normalized = parent.replace(/\/$/, '');
  if (
    !normalized.startsWith('viking://') ||
    normalized
      .slice(9)
      .split('/')
      .some((part) => !part || part === '.' || part === '..') ||
    /[\\%?#]/.test(normalized) ||
    controls(normalized) ||
    !name.trim() ||
    name !== name.trim() ||
    name === '.' ||
    name === '..' ||
    ['/', '\\', '%', '?', '#'].some((char) => name.includes(char)) ||
    controls(name)
  )
    throw new DirectoryFlowError('directoryNameInvalid');
  return `${normalized}/${name}`;
}
