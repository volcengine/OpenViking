export type RemoteResourceKind =
  | 'unknown'
  | 'feishu'
  | 'git'
  | 'webFeed'
  | 'webPage'
  | 'remoteFile'
  | 'tos'

export type RemoteResourceTypeSelection =
  | 'auto'
  | Exclude<RemoteResourceKind, 'unknown' | 'webFeed'>

const FEISHU_HOST_SUFFIXES = ['feishu.cn', 'larksuite.com', 'larkoffice.com']
const FEISHU_PATH_PREFIXES = ['docx', 'wiki', 'sheets', 'base']
const CODE_HOSTS = new Set([
  'github.com',
  'gitlab.com',
  'bitbucket.org',
  'codeberg.org',
  'gitee.com',
  'gitcode.com',
  'atomgit.com',
  'git.sr.ht',
])
const AZURE_DEVOPS_HOSTS = new Set(['dev.azure.com'])
const REMOTE_FILE_EXTENSIONS = new Set([
  'pdf',
  'md',
  'markdown',
  'txt',
  'text',
  'html',
  'htm',
  'doc',
  'docx',
  'xls',
  'xlsx',
  'xlsm',
  'pptx',
  'epub',
  'zip',
  'jpg',
  'jpeg',
  'png',
  'gif',
  'webp',
  'svg',
  'mp3',
  'wav',
  'm4a',
  'flac',
  'mp4',
  'avi',
  'mov',
  'mkv',
  'webm',
])

function hasHostSuffix(host: string, suffix: string): boolean {
  return host === suffix || host.endsWith(`.${suffix}`)
}

function looksLikeFeishuUrl(url: URL): boolean {
  const host = url.hostname.toLowerCase().replace(/\.$/, '')
  const firstPathSegment = url.pathname.split('/').filter(Boolean)[0]
  return (
    FEISHU_HOST_SUFFIXES.some((suffix) => hasHostSuffix(host, suffix)) &&
    FEISHU_PATH_PREFIXES.includes(firstPathSegment)
  )
}

function looksLikeWebFeed(url: URL): boolean {
  const basename = url.pathname
    .toLowerCase()
    .replace(/\/$/, '')
    .split('/')
    .pop()
  if (!basename) return false
  return (
    (basename.startsWith('sitemap') && basename.endsWith('.xml')) ||
    ['feed', 'feed.xml', 'rss', 'rss.xml', 'atom', 'atom.xml'].includes(
      basename,
    ) ||
    basename.endsWith('.rss') ||
    basename.endsWith('.atom')
  )
}

function looksLikeRemoteFile(url: URL): boolean {
  const filename = url.pathname.toLowerCase().split('/').pop() ?? ''
  const extension = filename.includes('.') ? filename.split('.').pop() : ''
  return REMOTE_FILE_EXTENSIONS.has(extension)
}

function looksLikeGitRepository(url: URL): boolean {
  if (['git:', 'ssh:'].includes(url.protocol)) return true
  if (!['http:', 'https:'].includes(url.protocol)) return false

  const parts = url.pathname.split('/').filter(Boolean)
  const host = url.hostname.toLowerCase()
  if (AZURE_DEVOPS_HOSTS.has(host)) {
    return (
      parts.length === 4 && parts[2] === '_git' && !url.searchParams.has('path')
    )
  }
  if (url.pathname.endsWith('.git')) return true
  if (!CODE_HOSTS.has(host)) return false
  if (parts.length < 2) return false
  if (host === 'github.com') {
    return parts.length === 2 || ['tree', 'commit'].includes(parts[2] ?? '')
  }
  return !['blob', 'raw', 'issues', 'pull', 'merge_requests'].some((segment) =>
    parts.includes(segment),
  )
}

export function detectRemoteResourceKind(
  rawSource: string,
): RemoteResourceKind {
  const source = rawSource.trim()
  if (!source) return 'unknown'
  if (source.startsWith('tos://')) return 'tos'
  if (/^git@[^:]+:.+/.test(source)) return 'git'

  let url: URL
  try {
    url = new URL(source)
  } catch {
    return 'unknown'
  }

  if (looksLikeFeishuUrl(url)) return 'feishu'
  if (looksLikeGitRepository(url)) return 'git'
  if (looksLikeWebFeed(url)) return 'webFeed'
  if (looksLikeRemoteFile(url)) return 'remoteFile'
  if (['http:', 'https:'].includes(url.protocol)) return 'webPage'
  return 'unknown'
}
