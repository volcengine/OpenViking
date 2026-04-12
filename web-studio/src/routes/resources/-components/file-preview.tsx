import { useEffect, useMemo, useState } from 'react'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import cpp from 'highlight.js/lib/languages/cpp'
import css from 'highlight.js/lib/languages/css'
import go from 'highlight.js/lib/languages/go'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { X } from 'lucide-react'

import { Button } from '#/components/ui/button'
import { ScrollArea } from '#/components/ui/scroll-area'
import { ovClient } from '#/lib/ov-client'

import { formatSize } from '../-lib/normalize'
import { useVikingFilePreview } from '../-hooks/viking-fm'
import type { VikingFsEntry } from '../-types/viking-fm'

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('css', css)
hljs.registerLanguage('go', go)
hljs.registerLanguage('java', java)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('python', python)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('yaml', yaml)

interface FilePreviewProps {
  file: VikingFsEntry | null
  onClose: () => void
  showCloseButton?: boolean
}

const vikingPrefix = 'viking://'

function toDownloadUrl(vikingUri: string): string {
  const baseUrl = ovClient.getOptions().baseUrl
  return `${baseUrl}/api/v1/content/download?uri=${encodeURIComponent(vikingUri)}`
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

function resolveRelativeVikingUri(baseFileUri: string, rawPath: string): string {
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

  if (['ts', 'tsx'].includes(ext)) return 'typescript'
  if (['js', 'jsx', 'mjs', 'cjs'].includes(ext)) return 'javascript'
  if (['py'].includes(ext)) return 'python'
  if (['go'].includes(ext)) return 'go'
  if (['rs'].includes(ext)) return 'rust'
  if (['java'].includes(ext)) return 'java'
  if (['c', 'h', 'hpp', 'cpp', 'cc'].includes(ext)) return 'cpp'
  if (['json'].includes(ext)) return 'json'
  if (['yml', 'yaml'].includes(ext)) return 'yaml'
  if (['md', 'markdown'].includes(ext)) return 'markdown'
  if (['html', 'xml', 'svg'].includes(ext)) return 'xml'
  if (['css', 'scss', 'less'].includes(ext)) return 'css'
  if (['sql'].includes(ext)) return 'sql'
  if (['sh', 'bash', 'zsh'].includes(ext)) return 'bash'

  return null
}

function escapeHtml(raw: string): string {
  return raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function FilePreview({ file, onClose, showCloseButton = true }: FilePreviewProps) {
  const previewQuery = useVikingFilePreview(file, {
    maxAutoReadBytes: 2 * 1024 * 1024,
    defaultReadLimit: 500,
  })
  const preview = previewQuery.preview
  const [markdownMode, setMarkdownMode] = useState<'preview' | 'source'>('preview')

  useEffect(() => {
    setMarkdownMode('preview')
  }, [file?.uri])

  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [imageLoading, setImageLoading] = useState(false)
  const [imageError, setImageError] = useState<string | null>(null)

  const imageSrc = useMemo(() => {
    if (!file || preview?.fileType !== 'image') {
      return null
    }
    return `${toDownloadUrl(file.uri)}&_t=${encodeURIComponent(file.modTime || Date.now().toString())}`
  }, [file, preview?.fileType])

  const highlightedCodeHtml = useMemo(() => {
    if (!preview || preview.fileType !== 'code') {
      return ''
    }

    const content = preview.content || ''
    if (!content) {
      return ''
    }

    const language = detectCodeLanguage(file?.name || '')
    try {
      if (language) {
        return hljs.highlight(content, { language }).value
      }
      return hljs.highlightAuto(content).value
    } catch {
      return escapeHtml(content)
    }
  }, [preview, file?.name])

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
        const response = await ovClient.instance.get(
          `${ovClient.getOptions().baseUrl}/api/v1/content/download`,
          {
            params: {
              uri: file.uri,
              _t: Date.now().toString(),
            },
            headers: {
              'Cache-Control': 'no-cache, no-store, max-age=0',
              Pragma: 'no-cache',
            },
            responseType: 'blob',
            validateStatus: (status) => status >= 200 && status < 300,
          },
        )

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

  if (!file) {
    return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">选择文件后在这里预览</div>
  }

  const isMarkdown = preview?.fileType === 'markdown'

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex items-start justify-between border-b px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{file.name}</div>
          <div className="text-xs text-muted-foreground">
            {formatSize(file.sizeBytes ?? file.size)} · {file.modTime || '-'}
          </div>
        </div>
        {showCloseButton ? (
          <Button size="icon" variant="ghost" onClick={onClose}>
            <X className="size-4" />
          </Button>
        ) : null}
      </div>

      <ScrollArea className="h-full min-h-0 p-4">
        {isMarkdown ? (
          <div className="mb-3 inline-flex overflow-hidden rounded-md border">
            <button
              type="button"
              className={`px-3 py-1.5 text-xs ${markdownMode === 'preview' ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60'}`}
              onClick={() => setMarkdownMode('preview')}
            >
              预览
            </button>
            <button
              type="button"
              className={`px-3 py-1.5 text-xs ${markdownMode === 'source' ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60'}`}
              onClick={() => setMarkdownMode('source')}
            >
              源码
            </button>
          </div>
        ) : null}

        {preview?.fileType === 'image' ? (
          imageLoading ? (
            <div className="text-sm text-muted-foreground">正在加载图片...</div>
          ) : imageUrl ? (
            <img src={imageUrl} alt={file.name} className="max-h-[70vh] max-w-full rounded-md border object-contain" />
          ) : imageSrc ? (
            <div className="space-y-3">
              <img
                src={imageSrc}
                alt={file.name}
                className="max-h-[70vh] max-w-full rounded-md border object-contain"
                onError={() => setImageError('direct img failed')}
              />
              {imageError ? <div className="text-xs text-muted-foreground">{imageError}</div> : null}
            </div>
          ) : (
            <div className="space-y-1 text-sm text-muted-foreground">
              <div>图片加载失败。</div>
              {imageError ? <div className="text-xs">{imageError}</div> : null}
            </div>
          )
        ) : null}

        {previewQuery.isLoading && preview?.fileType !== 'image' ? (
          <div className="text-sm text-muted-foreground">正在读取内容...</div>
        ) : null}

        {!previewQuery.isLoading && preview && preview.fileType !== 'image' && !preview.shouldAutoRead ? (
          <div className="text-sm text-muted-foreground">
            {preview.reason === 'binary' ? '二进制文件不支持文本预览。' : '文件较大，默认不自动加载。'}
          </div>
        ) : null}

        {!previewQuery.isLoading && preview?.fileType === 'markdown' && preview.shouldAutoRead && markdownMode === 'preview' ? (
          <article className="prose prose-sm max-w-none break-words dark:prose-invert dark:prose-pre:bg-muted-foreground/20">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                img: ({ src, alt }) => {
                  const resolvedSrc = src ? resolveMarkdownAssetUrl(String(src), file.uri) : String(src || '')
                  return <img src={resolvedSrc} alt={alt || ''} loading="lazy" className="max-w-full rounded-md border" />
                },
                a: ({ href, children }) => {
                  const resolvedHref = href ? resolveMarkdownAssetUrl(String(href), file.uri) : String(href || '')
                  const isExternal = /^(https?:|mailto:|tel:)/i.test(resolvedHref)
                  return (
                    <a href={resolvedHref} target={isExternal ? '_blank' : undefined} rel={isExternal ? 'noreferrer noopener' : undefined}>
                      {children}
                    </a>
                  )
                },
              }}
            >
              {preview.content || '(empty file)'}
            </ReactMarkdown>
          </article>
        ) : null}

        {!previewQuery.isLoading && preview?.fileType === 'markdown' && preview.shouldAutoRead && markdownMode === 'source' ? (
          <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/20 p-3 text-xs leading-6">{preview.content || '(empty file)'}</pre>
        ) : null}

        {!previewQuery.isLoading && preview?.fileType === 'code' && preview.shouldAutoRead ? (
          <pre className="overflow-auto rounded-md border bg-muted/20 p-3 text-xs leading-6">
            <code className="hljs block" dangerouslySetInnerHTML={{ __html: highlightedCodeHtml || '(empty file)' }} />
          </pre>
        ) : null}

        {!previewQuery.isLoading && preview && preview.fileType !== 'image' && preview.fileType !== 'markdown' && preview.fileType !== 'code' && preview.shouldAutoRead ? (
          <pre className="whitespace-pre-wrap break-words text-xs leading-6">{preview.content || '(empty file)'}</pre>
        ) : null}
      </ScrollArea>
    </div>
  )
}
