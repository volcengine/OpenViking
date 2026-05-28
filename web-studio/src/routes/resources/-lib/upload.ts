export const MAX_UPLOAD_FILES = 10
export const MAX_UPLOAD_FILE_SIZE_BYTES = 10 * 1024 * 1024

const BLOCKED_EXTENSIONS = new Set([
  '.pyc',
  '.pyo',
  '.pyd',
  '.so',
  '.dll',
  '.dylib',
  '.exe',
  '.bin',
  '.iso',
  '.img',
  '.db',
  '.sqlite',
  '.tar',
  '.gz',
  '.rar',
  '.7z',
  '.class',
  '.jar',
  '.war',
  '.ear',
  '.ico',
  '.wma',
  '.mid',
  '.midi',
])

const ERROR_CODE_PREFIX_RE = /^([A-Z][A-Z0-9_]{2,})\s*:\s*(.+)$/s
const ERROR_CODE_RE = /\b([A-Z][A-Z0-9_]{2,})\b/

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

export function getExtensionFromName(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

export function isBlockedFile(name: string): boolean {
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return false
  return BLOCKED_EXTENSIONS.has(name.slice(dot).toLowerCase())
}

export function parseUploadError(message: string): {
  errorCode: string | null
  errorMessage: string
} {
  const trimmed = message.trim()
  const prefixed = trimmed.match(ERROR_CODE_PREFIX_RE)
  if (prefixed) {
    return {
      errorCode: prefixed[1],
      errorMessage: prefixed[2],
    }
  }

  const matchedCode = trimmed.match(ERROR_CODE_RE)
  return {
    errorCode: matchedCode?.[1] ?? null,
    errorMessage: trimmed,
  }
}
