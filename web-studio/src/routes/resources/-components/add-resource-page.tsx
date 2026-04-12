import { fileTypeFromBlob } from 'file-type'
import { AlertTriangle, CheckCircle2, ChevronRight, FileIcon, FolderOpen, Globe, Info, Loader2Icon, Upload, X } from 'lucide-react'
import { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card, CardContent } from '#/components/ui/card'
import { Checkbox } from '#/components/ui/checkbox'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '#/components/ui/collapsible'
import { DirectoryPickerDialog } from './directory-picker-dialog'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { Progress } from '#/components/ui/progress'
import { Textarea } from '#/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '#/components/ui/tooltip'
import { useResourceUpload } from '#/hooks/use-resource-upload'

type Mode = 'upload' | 'remote'

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

function getExtensionFromName(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/**
 * File extensions that the backend cannot process — pure binary or unsupported archives.
 * Mirrors the subset of openviking/parse/parsers/constants.py IGNORE_EXTENSIONS
 * that have NO dedicated parser registered in ParserRegistry.
 */
const BLOCKED_EXTENSIONS = new Set([
  // Binary / compiled
  '.pyc', '.pyo', '.pyd', '.so', '.dll', '.dylib', '.exe', '.bin',
  // Disk images / databases
  '.iso', '.img', '.db', '.sqlite',
  // Archives without a parser (zip has ZipParser)
  '.tar', '.gz', '.rar', '.7z',
  // Java compiled
  '.class', '.jar', '.war', '.ear',
  // Misc unsupported
  '.ico', '.wma', '.mid', '.midi',
])

function isBlockedFile(name: string): boolean {
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return false
  return BLOCKED_EXTENSIONS.has(name.slice(dot).toLowerCase())
}

export function AddResourcePage() {
  const { t } = useTranslation('addResource')
  const { state: uploadState, startUpload, startRemote, reset, isActive } = useResourceUpload()

  const [mode, setMode] = useState<Mode>(uploadState.mode)
  const [remoteUrl, setRemoteUrl] = useState(uploadState.remoteUrl)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [detectedType, setDetectedType] = useState<string | null>(uploadState.fileType)
  const [targetUri, setTargetUri] = useState('viking://resources/')
  const [strict, setStrict] = useState(false)
  const [directlyUploadMedia, setDirectlyUploadMedia] = useState(true)
  const [reason, setReason] = useState('')
  const [instruction, setInstruction] = useState('')
  const [ignoreDirs, setIgnoreDirs] = useState('')
  const [include, setInclude] = useState('')
  const [exclude, setExclude] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [dirPickerOpen, setDirPickerOpen] = useState(false)

  const phase = uploadState.phase
  const skippedFiles = uploadState.skippedFiles

  // When an upload is active or done, use context state for display;
  // the local selectedFile is only valid for idle state before submission.
  const displayFileName = phase !== 'idle' && uploadState.mode === 'upload' ? uploadState.fileName : selectedFile?.name ?? null
  const displayFileSize = phase !== 'idle' && uploadState.mode === 'upload' ? uploadState.fileSize : selectedFile?.size ?? null
  const displayFileType = phase !== 'idle' && uploadState.mode === 'upload' ? uploadState.fileType : detectedType
  const displayRemoteUrl = phase !== 'idle' && uploadState.mode === 'remote' ? uploadState.remoteUrl : remoteUrl
  const activeMode = phase !== 'idle' ? uploadState.mode : mode

  const detectFileType = useCallback(async (file: File) => {
    try {
      const result = await fileTypeFromBlob(file)
      setDetectedType(result?.mime ?? null)
    } catch {
      setDetectedType(null)
    }
  }, [])

  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      const file = acceptedFiles[0]
      if (file) {
        if (isBlockedFile(file.name)) {
          toast.error(t('fileBlocked', { name: file.name }))
          return
        }
        setSelectedFile(file)
        detectFileType(file)
      }
    },
    [detectFileType, t],
  )

  const removeFile = useCallback(() => {
    setSelectedFile(null)
    setDetectedType(null)
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    multiple: false,
  })

  const buildCommonBody = () => {
    const body: Record<string, unknown> = {
      parent: targetUri.trim() || undefined,
      strict,
      telemetry: true,
      wait: true,
      directly_upload_media: directlyUploadMedia,
    }
    if (reason.trim()) {
      body.reason = reason.trim()
    }
    if (instruction.trim()) {
      body.instruction = instruction.trim()
    }
    if (mode === 'remote') {
      if (ignoreDirs.trim()) {
        body.ignore_dirs = ignoreDirs.trim()
      }
      if (include.trim()) {
        body.include = include.trim()
      }
      if (exclude.trim()) {
        body.exclude = exclude.trim()
      }
    }
    return body
  }

  const handleSubmit = () => {
    if (mode === 'upload') {
      if (!selectedFile) return
      startUpload({ file: selectedFile, fileType: detectedType, commonBody: buildCommonBody() })
    } else {
      const url = remoteUrl.trim()
      if (!url) return
      startRemote({ url, commonBody: buildCommonBody() })
    }
  }

  const handleReset = () => {
    reset()
    setSelectedFile(null)
    setDetectedType(null)
    setRemoteUrl('')
    setMode('upload')
  }

  const fileTypeLabel =
    displayFileType ?? (displayFileName ? getExtensionFromName(displayFileName) || t('fileInfo.unknown') : null)

  const canSubmit =
    mode === 'upload' ? !!selectedFile : !!remoteUrl.trim()

  return (
    <div className="flex flex-col gap-6">

      <div className="max-w-4xl">
        <Card>
          <CardContent className="space-y-5 pt-6">
            {/* Mode Switch */}
            <div className="flex gap-1 rounded-lg bg-muted p-1">
              <button
                type="button"
                disabled={phase !== 'idle'}
                className={`flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  activeMode === 'upload'
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                } ${phase !== 'idle' ? 'cursor-not-allowed opacity-60' : ''}`}
                onClick={() => setMode('upload')}
              >
                <Upload className="size-4" />
                {t('mode.upload')}
              </button>
              <button
                type="button"
                disabled={phase !== 'idle'}
                className={`flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  activeMode === 'remote'
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                } ${phase !== 'idle' ? 'cursor-not-allowed opacity-60' : ''}`}
                onClick={() => setMode('remote')}
              >
                <Globe className="size-4" />
                {t('mode.remote')}
              </button>
            </div>

            {/* Upload Mode: Dropzone */}
            {activeMode === 'upload' ? (
              <div
                {...(phase === 'idle' ? getRootProps() : {})}
                className={`relative rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
                  phase !== 'idle'
                    ? 'cursor-default border-border bg-muted/20'
                    : isDragActive
                      ? 'cursor-pointer border-primary bg-primary/5'
                      : displayFileName
                        ? 'cursor-pointer border-border bg-muted/20'
                        : 'cursor-pointer border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/30'
                }`}
              >
                {phase === 'idle' && <input {...getInputProps()} />}

                {displayFileName ? (
                  <div className="flex items-center gap-4 text-left">
                    <div className="flex size-12 shrink-0 items-center justify-center rounded-lg bg-muted">
                      <FileIcon className="size-6 text-muted-foreground" />
                    </div>
                    <div className="min-w-0 flex-1 space-y-1">
                      <p className="truncate text-sm font-medium">{displayFileName}</p>
                      <div className="flex flex-wrap items-center gap-2">
                        {displayFileSize != null && (
                          <span className="text-xs text-muted-foreground">{formatFileSize(displayFileSize)}</span>
                        )}
                        {fileTypeLabel ? (
                          <Badge variant="secondary" className="text-xs">
                            {fileTypeLabel}
                          </Badge>
                        ) : null}
                      </div>
                    </div>
                    {phase === 'idle' && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-8 shrink-0"
                        onClick={(e) => {
                          e.stopPropagation()
                          removeFile()
                        }}
                        aria-label={t('fileInfo.remove')}
                      >
                        <X className="size-4" />
                      </Button>
                    )}
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Upload className="mx-auto size-10 text-muted-foreground/60" />
                    <p className="text-sm font-medium">{t('dropzone.title')}</p>
                    <p className="text-xs text-muted-foreground">{t('dropzone.hint')}</p>
                    <p className="text-xs text-muted-foreground/70">{t('dropzone.supportedFormats')}</p>
                  </div>
                )}
              </div>
            ) : (
              /* Remote Mode: URL Input */
              <div className="space-y-2">
                <Label htmlFor="add-resource-remote-url">{t('remoteUrl')}</Label>
                <Input
                  id="add-resource-remote-url"
                  placeholder={t('remoteUrl.placeholder')}
                  value={displayRemoteUrl}
                  onChange={(e) => setRemoteUrl(e.target.value)}
                  disabled={phase !== 'idle'}
                />
                <p className="text-xs text-muted-foreground">{t('remoteUrl.hint')}</p>
              </div>
            )}

            {/* Upload Progress / Processing / Result */}
            {phase === 'uploading' && (
              <div className="space-y-2">
                <Progress value={uploadState.progress}>
                  <span className="text-sm text-muted-foreground">
                    {t('upload.progress', { progress: uploadState.progress })}
                  </span>
                </Progress>
                <Button variant="outline" size="sm" onClick={handleReset}>
                  {t('cancelUpload')}
                </Button>
              </div>
            )}

            {phase === 'processing' && (
              <div className="space-y-2">
                <Progress value={100} />
                <div className="flex items-center gap-2">
                  <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
                  <p className="text-sm text-muted-foreground">
                    {t('upload.processing')}
                  </p>
                </div>
                <Button variant="outline" size="sm" onClick={handleReset}>
                  {t('cancelUpload')}
                </Button>
              </div>
            )}

            {phase === 'done' && (
              <div className="space-y-3 rounded-lg border border-border/50 bg-muted/10 p-4">
                <div className="flex items-center gap-2 text-sm font-medium text-green-600 dark:text-green-400">
                  <CheckCircle2 className="size-4" />
                  {t('result.success')}
                </div>

                {skippedFiles.length > 0 && (
                  <Collapsible>
                    <CollapsibleTrigger className="flex items-center gap-1 text-sm text-amber-600 dark:text-amber-400 hover:underline">
                      <AlertTriangle className="size-4" />
                      {t('result.skippedFiles', { count: skippedFiles.length })}
                      <ChevronRight className="size-3" />
                    </CollapsibleTrigger>
                    <CollapsibleContent>
                      <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                        {skippedFiles.map((file) => (
                          <li key={file} className="truncate">• {file}</li>
                        ))}
                      </ul>
                    </CollapsibleContent>
                  </Collapsible>
                )}

                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleReset}
                >
                  {t('continueUpload')}
                </Button>
              </div>
            )}

            {/* Target URI */}
            <div className="space-y-2">
              <Label htmlFor="add-resource-target">{t('targetUri')}</Label>
              <div className="flex gap-2">
                <Input
                  id="add-resource-target"
                  placeholder="viking://resources/"
                  value={targetUri}
                  onChange={(event) => setTargetUri(event.target.value)}
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  onClick={() => setDirPickerOpen(true)}
                >
                  <FolderOpen className="mr-1.5 size-4" />
                  {t('targetUri.browse')}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">{t('targetUri.hint')}</p>
            </div>

            {/* Advanced Options */}
            <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
              <CollapsibleTrigger className="flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground">
                <ChevronRight
                  className={`size-4 transition-transform ${advancedOpen ? 'rotate-90' : ''}`}
                />
                {t('advancedOptions')}
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="mt-3 space-y-4 rounded-lg border border-border/50 bg-muted/10 p-4">
                  {/* Checkboxes */}
                  <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
                    <Label className="flex items-center gap-2">
                      <Checkbox checked={strict} onCheckedChange={(checked) => setStrict(Boolean(checked))} />
                      <span>{t('strict')}</span>
                      <Tooltip>
                        <TooltipTrigger render={<Info className="size-3.5 text-muted-foreground" />} />
                        <TooltipContent>{t('strict.hint')}</TooltipContent>
                      </Tooltip>
                    </Label>
                    <Label className="flex items-center gap-2">
                      <Checkbox checked={directlyUploadMedia} onCheckedChange={(checked) => setDirectlyUploadMedia(Boolean(checked))} />
                      <span>{t('directlyUploadMedia')}</span>
                      <Tooltip>
                        <TooltipTrigger render={<Info className="size-3.5 text-muted-foreground" />} />
                        <TooltipContent>{t('directlyUploadMedia.hint')}</TooltipContent>
                      </Tooltip>
                    </Label>
                  </div>

                  {/* Directory scan params (remote mode only) */}
                  {activeMode === 'remote' && (
                    <div className="space-y-4 border-t border-border/50 pt-4">
                      <div className="space-y-2">
                        <Label htmlFor="add-resource-ignore-dirs">{t('directoryScan.ignoreDirs')}</Label>
                        <Input
                          id="add-resource-ignore-dirs"
                          placeholder={t('directoryScan.ignoreDirs.placeholder')}
                          value={ignoreDirs}
                          onChange={(e) => setIgnoreDirs(e.target.value)}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="add-resource-include">{t('directoryScan.include')}</Label>
                        <Input
                          id="add-resource-include"
                          placeholder={t('directoryScan.include.placeholder')}
                          value={include}
                          onChange={(e) => setInclude(e.target.value)}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="add-resource-exclude">{t('directoryScan.exclude')}</Label>
                        <Input
                          id="add-resource-exclude"
                          placeholder={t('directoryScan.exclude.placeholder')}
                          value={exclude}
                          onChange={(e) => setExclude(e.target.value)}
                        />
                      </div>
                    </div>
                  )}

                  {/* Reason */}
                  <div className="space-y-2">
                    <Label htmlFor="add-resource-reason">{t('reason')}</Label>
                    <Textarea
                      id="add-resource-reason"
                      placeholder={t('reason.placeholder')}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </div>

                  {/* Instruction */}
                  <div className="space-y-2">
                    <Label htmlFor="add-resource-instruction">{t('instruction')}</Label>
                    <Textarea
                      id="add-resource-instruction"
                      placeholder={t('instruction.placeholder')}
                      value={instruction}
                      onChange={(e) => setInstruction(e.target.value)}
                    />
                  </div>
                </div>
              </CollapsibleContent>
            </Collapsible>

            {phase === 'idle' && (
              <Button
                onClick={handleSubmit}
                disabled={!canSubmit || isActive}
              >
                {isActive
                  ? t('uploading')
                  : mode === 'upload'
                    ? t('upload')
                    : t('submit')}
              </Button>
            )}
          </CardContent>
        </Card>
      </div>

      <DirectoryPickerDialog
        open={dirPickerOpen}
        onOpenChange={setDirPickerOpen}
        value={targetUri}
        onSelect={setTargetUri}
      />
    </div>
  )
}
