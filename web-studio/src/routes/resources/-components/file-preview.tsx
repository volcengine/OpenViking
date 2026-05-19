import { useEffect, useMemo, useRef, useState, lazy, Suspense } from 'react'
import hljs from 'highlight.js/lib/core'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { X, Pencil, Save, XCircle, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { ScrollArea } from '#/components/ui/scroll-area'
import { client } from '#/gen/ov-client/client.gen'
import { getContentDownload, ovClient } from '#/lib/ov-client'
import type { GetContentDownloadData } from '#/gen/ov-client/types.gen'
import type { ContentDownloadQuery } from '@ov-server/api/v1/content'

import { formatSize } from '../-lib/normalize'
import { saveFileContent } from '../-lib/api'
import {
  useVikingFilePreview,
  useInvalidateVikingFs,
} from '../-hooks/viking-fm'
import type { VikingFsEntry } from '../-types/viking-fm'
import type { CodeEditorHandle } from './code-editor'

const LazyCodeEditor = lazy(() =>
  import('./code-editor').then((m) => ({ default: m.CodeEditor })),
)

const languageLoaders: Partial<
  Record<
    string,
    () => Promise<{ default: Parameters<typeof hljs.registerLanguage>[1] }>
  >
> = {
  bash: () => import('highlight.js/lib/languages/bash'),
  c: () => import('highlight.js/lib/languages/c'),
  cpp: () => import('highlight.js/lib/languages/cpp'),
  csharp: () => import('highlight.js/lib/languages/csharp'),
  css: () => import('highlight.js/lib/languages/css'),
  dart: () => import('highlight.js/lib/languages/dart'),
  diff: () => import('highlight.js/lib/languages/diff'),
  dockerfile: () => import('highlight.js/lib/languages/dockerfile'),
  elixir: () => import('highlight.js/lib/languages/elixir'),
  erlang: () => import('highlight.js/lib/languages/erlang'),
  go: () => import('highlight.js/lib/languages/go'),
  graphql: () => import('highlight.js/lib/languages/graphql'),
  haskell: () => import('highlight.js/lib/languages/haskell'),
  ini: () => import('highlight.js/lib/languages/ini'),
  java: () => import('highlight.js/lib/languages/java'),
  javascript: () => import('highlight.js/lib/languages/javascript'),
  json: () => import('highlight.js/lib/languages/json'),
  kotlin: () => import('highlight.js/lib/languages/kotlin'),
  latex: () => import('highlight.js/lib/languages/latex'),
  less: () => import('highlight.js/lib/languages/less'),
  lua: () => import('highlight.js/lib/languages/lua'),
  makefile: () => import('highlight.js/lib/languages/makefile'),
  markdown: () => import('highlight.js/lib/languages/markdown'),
  nginx: () => import('highlight.js/lib/languages/nginx'),
  objectivec: () => import('highlight.js/lib/languages/objectivec'),
  perl: () => import('highlight.js/lib/languages/perl'),
  php: () => import('highlight.js/lib/languages/php'),
  plaintext: () => import('highlight.js/lib/languages/plaintext'),
  protobuf: () => import('highlight.js/lib/languages/protobuf'),
  python: () => import('highlight.js/lib/languages/python'),
  r: () => import('highlight.js/lib/languages/r'),
  ruby: () => import('highlight.js/lib/languages/ruby'),
  rust: () => import('highlight.js/lib/languages/rust'),
  scala: () => import('highlight.js/lib/languages/scala'),
  scss: () => import('highlight.js/lib/languages/scss'),
  shell: () => import('highlight.js/lib/languages/shell'),
  sql: () => import('highlight.js/lib/languages/sql'),
  swift: () => import('highlight.js/lib/languages/swift'),
  typescript: () => import('highlight.js/lib/languages/typescript'),
  wasm: () => import('highlight.js/lib/languages/wasm'),
  xml: () => import('highlight.js/lib/languages/xml'),
  yaml: () => import('highlight.js/lib/languages/yaml'),
}

const loadedLanguages = new Set<string>()

async function ensureLanguage(lang: string): Promise<void> {
  if (loadedLanguages.has(lang)) return
  const loader = languageLoaders[lang]
  if (!loader) return
  const mod = await loader()
  hljs.registerLanguage(lang, mod.default)
  loadedLanguages.add(lang)
}

interface FilePreviewProps {
  file: VikingFsEntry | null
  onClose: () => void
  showCloseButton?: boolean
}

const vikingPrefix = 'viking://'
const contentDownloadUrl: GetContentDownloadData['url'] =
  '/api/v1/content/download'

function toDownloadUrl(vikingUri: string): string {
  const query: ContentDownloadQuery = { uri: vikingUri }
  return client.buildUrl({
    baseURL: ovClient.getOptions().baseUrl,
    query,
    url: contentDownloadUrl,
  })
}

function withCacheBust(url: string, cacheKey: string): string {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}_t=${encodeURIComponent(cacheKey)}`
}

function dirnameVikingUri(fileUri: string): string {
  if (fileUri === vikingPrefix) {
    return vikingPrefix
  }

  const trimmed = fileUri.endsWith('/') ? fileUri.slice(0, -1) : fileUri
  const idx = trimmed.lastIndexOf('/')
  if (idx < vikingPrefix.length) {
    return vikingPrefix
  }
  return `${trimmed.slice(0, idx + 1)}`
}

function resolveRelativeVikingUri(
  baseFileUri: string,
  rawPath: string,
): string {
  const baseDir = dirnameVikingUri(baseFileUri)
  const baseBody = baseDir.slice(vikingPrefix.length, -1)

  const pathPart = rawPath.split('#')[0]?.split('?')[0] || ''
  const suffix = rawPath.slice(pathPart.length)

  const baseSegments = baseBody ? baseBody.split('/').filter(Boolean) : []
  const relativeSegments = pathPart.split('/').filter(Boolean)

  const merged = [...baseSegments]
  for (const segment of relativeSegments) {
    if (segment === '.') {
      continue
    }
    if (segment === '..') {
      merged.pop()
      continue
    }
    merged.push(segment)
  }

  const resolved = `${vikingPrefix}${merged.join('/')}`
  return `${resolved}${suffix}`
}

function resolveMarkdownAssetUrl(assetPath: string, fileUri: string): string {
  const trimmed = assetPath.trim()
  if (!trimmed || trimmed.startsWith('#')) {
    return trimmed
  }

  if (/^(https?:|data:|blob:|mailto:|tel:)/i.test(trimmed)) {
    return trimmed
  }

  if (trimmed.startsWith(vikingPrefix)) {
    return toDownloadUrl(trimmed)
  }

  const vikingUri = resolveRelativeVikingUri(fileUri, trimmed)
  return toDownloadUrl(vikingUri)
}

function detectCodeLanguage(filename: string): string | null {
  const lower = filename.toLowerCase()
  const ext = lower.includes('.') ? lower.split('.').pop() || '' : ''

  const extMap: Record<string, string> = {
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    mjs: 'javascript',
    cjs: 'javascript',
    py: 'python',
    pyw: 'python',
    go: 'go',
    rs: 'rust',
    java: 'java',
    c: 'c',
    h: 'c',
    cpp: 'cpp',
    cc: 'cpp',
    cxx: 'cpp',
    hpp: 'cpp',
    hxx: 'cpp',
    cs: 'csharp',
    json: 'json',
    yml: 'yaml',
    yaml: 'yaml',
    md: 'markdown',
    markdown: 'markdown',
    html: 'xml',
    xml: 'xml',
    svg: 'xml',
    xhtml: 'xml',
    css: 'css',
    scss: 'scss',
    less: 'less',
    sql: 'sql',
    sh: 'bash',
    bash: 'bash',
    zsh: 'bash',
    toml: 'ini',
    ini: 'ini',
    cfg: 'ini',
    conf: 'ini',
    dockerfile: 'dockerfile',
    dart: 'dart',
    kt: 'kotlin',
    kts: 'kotlin',
    swift: 'swift',
    rb: 'ruby',
    rake: 'ruby',
    gemspec: 'ruby',
    php: 'php',
    lua: 'lua',
    r: 'r',
    rmd: 'r',
    scala: 'scala',
    ex: 'elixir',
    exs: 'elixir',
    erl: 'erlang',
    hrl: 'erlang',
    hs: 'haskell',
    lhs: 'haskell',
    m: 'objectivec',
    mm: 'objectivec',
    pl: 'perl',
    pm: 'perl',
    proto: 'protobuf',
    graphql: 'graphql',
    gql: 'graphql',
    tex: 'latex',
    latex: 'latex',
    makefile: 'makefile',
    nginx: 'nginx',
    wasm: 'wasm',
    wat: 'wasm',
    diff: 'diff',
    patch: 'diff',
  }

  if (ext && extMap[ext]) return extMap[ext]

  const basename = lower.split('/').pop() || ''
  if (basename === 'dockerfile' || basename.startsWith('dockerfile.'))
    return 'dockerfile'
  if (basename === 'makefile' || basename === 'gnumakefile') return 'makefile'
  if (
    basename === '.bashrc' ||
    basename === '.zshrc' ||
    basename === '.bash_profile'
  )
    return 'bash'

  return null
}

function escapeHtml(raw: string): string {
  return raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function FilePreview({
  file,
  onClose,
  showCloseButton = true,
}: FilePreviewProps) {
  const { t } = useTranslation('resources')
  const previewQuery = useVikingFilePreview(file, {
    maxAutoReadBytes: 2 * 1024 * 1024,
    defaultReadLimit: -1,
  })
  const preview = previewQuery.preview
  const [markdownMode, setMarkdownMode] = useState<'preview' | 'source'>(
    'preview',
  )
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const editorRef = useRef<CodeEditorHandle>(null)
  const { invalidatePreview } = useInvalidateVikingFs()

  const canEdit =
    preview?.shouldAutoRead &&
    (preview.fileType === 'code' ||
      preview.fileType === 'markdown' ||
      preview.fileType === 'text')

  useEffect(() => {
    setMarkdownMode('preview')
    setEditing(false)
  }, [file?.uri])

  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [imageLoading, setImageLoading] = useState(false)
  const [imageError, setImageError] = useState<string | null>(null)

  const imageSrc = useMemo(() => {
    if (!file || preview?.fileType !== 'image') {
      return null
    }
    return withCacheBust(
      toDownloadUrl(file.uri),
      file.modTime || Date.now().toString(),
    )
  }, [file, preview?.fileType])

  const [highlightedCodeHtml, setHighlightedCodeHtml] = useState('')

  const needsHighlight =
    preview?.fileType === 'code' ||
    (preview?.fileType === 'markdown' && markdownMode === 'source')

  useEffect(() => {
    if (!preview || !needsHighlight) {
      setHighlightedCodeHtml('')
      return
    }

    const content = preview.content || ''
    if (!content) {
      setHighlightedCodeHtml('')
      return
    }

    let cancelled = false
    const language = detectCodeLanguage(file?.name || '')

    const run = async () => {
      try {
        if (language) {
          await ensureLanguage(language)
          if (cancelled) return
          setHighlightedCodeHtml(hljs.highlight(content, { language }).value)
        } else {
          setHighlightedCodeHtml(hljs.highlightAuto(content).value)
        }
      } catch {
        if (!cancelled) setHighlightedCodeHtml(escapeHtml(content))
      }
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [preview, file?.name, needsHighlight])

  useEffect(() => {
    let alive = true

    const loadWithAuthClient = async () => {
      if (!file || preview?.fileType !== 'image') {
        setImageUrl(null)
        setImageError(null)
        setImageLoading(false)
        return
      }

      setImageLoading(true)
      setImageError(null)

      try {
        const response = await getContentDownload({
          query: { uri: file.uri },
          responseType: 'blob',
          throwOnError: true,
        })

        if (!alive) {
          return
        }

        const blob = response.data as Blob
        if (blob.size === 0) {
          throw new Error('empty blob')
        }

        const nextUrl = URL.createObjectURL(blob)
        setImageUrl((prev) => {
          if (prev) {
            URL.revokeObjectURL(prev)
          }
          return nextUrl
        })
        setImageLoading(false)
      } catch (error) {
        if (!alive) {
          return
        }
        setImageLoading(false)
        setImageError(String(error))
      }
    }

    if (preview?.fileType === 'image') {
      void loadWithAuthClient()
    }

    return () => {
      alive = false
    }
  }, [file, preview?.fileType])

  const handleSave = async () => {
    if (!file || !editorRef.current) return
    setSaving(true)
    try {
      await saveFileContent(file.uri, editorRef.current.getContent())
      invalidatePreview(file.uri)
      setEditing(false)
    } catch (err) {
      console.error('Save failed:', err)
    } finally {
      setSaving(false)
    }
  }

  if (!file) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t('filePreview.emptyPrompt')}
      </div>
    )
  }

  const isMarkdown = preview?.fileType === 'markdown'
  const isDark = document.documentElement.classList.contains('dark')
  const emptyFileText = t('filePreview.emptyFile')

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{file.name}</div>
            <div className="text-xs text-muted-foreground">
              {formatSize(file.sizeBytes ?? file.size)} · {file.modTime || '-'}
            </div>
          </div>
          {editing ? (
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="ghost"
                disabled={saving}
                onClick={() => setEditing(false)}
              >
                <XCircle className="mr-1 size-3.5" />
                {t('filePreview.cancel')}
              </Button>
              <Button
                size="sm"
                className="active:scale-[0.96] transition-transform"
                disabled={saving}
                onClick={handleSave}
              >
                {saving ? (
                  <Loader2 className="mr-1 size-3.5 animate-spin" />
                ) : (
                  <Save className="mr-1 size-3.5" />
                )}
                {t('filePreview.save')}
              </Button>
            </div>
          ) : (
            canEdit && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditing(true)}
              >
                <Pencil className="mr-1 size-3.5" />
                {t('filePreview.edit')}
              </Button>
            )
          )}
        </div>
        {showCloseButton ? (
          <Button
            size="icon"
            variant="ghost"
            className="size-10"
            onClick={onClose}
          >
            <X className="size-4" />
          </Button>
        ) : null}
      </div>

      {editing && preview?.content != null ? (
        <div className="h-full min-h-0 p-2">
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 size-4 animate-spin" />
                {t('filePreview.loadingEditor')}
              </div>
            }
          >
            <LazyCodeEditor
              ref={editorRef}
              initialContent={preview.content}
              filename={file.name}
              isDark={isDark}
            />
          </Suspense>
        </div>
      ) : (
        <ScrollArea className="h-full min-h-0 p-4">
          {isMarkdown && !editing ? (
            <div className="mb-3 inline-flex overflow-hidden rounded-md border">
              <button
                type="button"
                className={`px-3 py-1.5 text-xs ${markdownMode === 'preview' ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60'}`}
                onClick={() => setMarkdownMode('preview')}
              >
                {t('filePreview.markdownPreview')}
              </button>
              <button
                type="button"
                className={`px-3 py-1.5 text-xs ${markdownMode === 'source' ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60'}`}
                onClick={() => setMarkdownMode('source')}
              >
                {t('filePreview.markdownSource')}
              </button>
            </div>
          ) : null}

          {preview?.fileType === 'image' ? (
            imageLoading ? (
              <div className="text-sm text-muted-foreground">
                {t('filePreview.imageLoading')}
              </div>
            ) : imageUrl ? (
              <img
                src={imageUrl}
                alt={file.name}
                className="max-h-[70vh] max-w-full rounded-md object-contain outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
              />
            ) : imageSrc ? (
              <div className="space-y-3">
                <img
                  src={imageSrc}
                  alt={file.name}
                  className="max-h-[70vh] max-w-full rounded-md object-contain outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
                  onError={() => setImageError('direct img failed')}
                />
                {imageError ? (
                  <div className="text-xs text-muted-foreground">
                    {imageError}
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="space-y-1 text-sm text-muted-foreground">
                <div>{t('filePreview.imageFailed')}</div>
                {imageError ? (
                  <div className="text-xs">{imageError}</div>
                ) : null}
              </div>
            )
          ) : null}

          {previewQuery.isLoading && preview?.fileType !== 'image' ? (
            <div className="text-sm text-muted-foreground">
              {t('filePreview.loadingContent')}
            </div>
          ) : null}

          {!previewQuery.isLoading &&
          preview &&
          preview.fileType !== 'image' &&
          !preview.shouldAutoRead ? (
            <div className="text-sm text-muted-foreground">
              {preview.reason === 'binary'
                ? t('filePreview.unsupportedBinary')
                : t('filePreview.largeFileSkipped')}
            </div>
          ) : null}

          {!previewQuery.isLoading &&
          preview?.fileType === 'markdown' &&
          preview.shouldAutoRead &&
          markdownMode === 'preview' ? (
            <article className="prose prose-sm max-w-none break-words dark:prose-invert dark:prose-pre:bg-muted-foreground/20">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  img: ({ src, alt }) => {
                    const resolvedSrc = src
                      ? resolveMarkdownAssetUrl(String(src), file.uri)
                      : String(src || '')
                    return (
                      <img
                        src={resolvedSrc}
                        alt={alt || ''}
                        loading="lazy"
                        className="max-w-full rounded-md outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
                      />
                    )
                  },
                  a: ({ href, children }) => {
                    const resolvedHref = href
                      ? resolveMarkdownAssetUrl(String(href), file.uri)
                      : String(href || '')
                    const isExternal = /^(https?:|mailto:|tel:)/i.test(
                      resolvedHref,
                    )
                    return (
                      <a
                        href={resolvedHref}
                        target={isExternal ? '_blank' : undefined}
                        rel={isExternal ? 'noreferrer noopener' : undefined}
                      >
                        {children}
                      </a>
                    )
                  },
                }}
              >
                {preview.content || emptyFileText}
              </ReactMarkdown>
            </article>
          ) : null}

          {!previewQuery.isLoading &&
          preview?.fileType === 'markdown' &&
          preview.shouldAutoRead &&
          markdownMode === 'source' ? (
            <pre className="overflow-auto rounded-md border bg-muted/20 p-3 text-xs leading-6">
              <code
                className="hljs block"
                dangerouslySetInnerHTML={{
                  __html:
                    highlightedCodeHtml ||
                    escapeHtml(preview.content || emptyFileText),
                }}
              />
            </pre>
          ) : null}

          {!previewQuery.isLoading &&
          preview?.fileType === 'code' &&
          preview.shouldAutoRead ? (
            <pre className="overflow-auto rounded-md border bg-muted/20 p-3 text-xs leading-6">
              <code
                className="hljs block"
                dangerouslySetInnerHTML={{
                  __html: highlightedCodeHtml || escapeHtml(emptyFileText),
                }}
              />
            </pre>
          ) : null}

          {!previewQuery.isLoading &&
          preview &&
          preview.fileType !== 'image' &&
          preview.fileType !== 'markdown' &&
          preview.fileType !== 'code' &&
          preview.shouldAutoRead ? (
            <pre className="whitespace-pre-wrap break-words text-xs leading-6">
              {preview.content || emptyFileText}
            </pre>
          ) : null}
        </ScrollArea>
      )}
    </div>
  )
}
