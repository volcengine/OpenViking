import { fileTypeFromBlob } from 'file-type'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  FileIcon,
  FolderOpen,
  Globe,
  Info,
  Loader2Icon,
  Upload,
} from 'lucide-react'
import { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import { Checkbox } from '#/components/ui/checkbox'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '#/components/ui/collapsible'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { Textarea } from '#/components/ui/textarea'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '#/components/ui/tooltip'
import { cn } from '#/lib/utils'
import { useResourceUpload } from '../-hooks/use-resource-upload'
import {
  MAX_UPLOAD_FILES,
  MAX_UPLOAD_FILE_SIZE_BYTES,
  formatFileSize,
  isBlockedFile,
} from '../-lib/upload'
import { DirectoryPickerDialog } from './directory-picker-dialog'

type Mode = 'upload' | 'remote'

type SelectedUploadFile = {
  id: string
  file: File
  fileType: string | null
}

function createLocalFileId(): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return crypto.randomUUID()
  }
  return `local-file-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

async function detectFileType(file: File): Promise<string | null> {
  try {
    const result = await fileTypeFromBlob(file)
    return result?.mime ?? null
  } catch {
    return null
  }
}

export function AddResourceForm({
  onSubmitted,
}: { onSubmitted?: () => void } = {}) {
  const { t } = useTranslation('addResource')
  const { enqueueUploads, startRemote, resetRemote, remoteState } =
    useResourceUpload()

  const [mode, setMode] = useState<Mode>('upload')
  const [remoteUrl, setRemoteUrl] = useState('')
  const [selectedFiles, setSelectedFiles] = useState<SelectedUploadFile[]>([])
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

  const remotePhase = remoteState.phase
  const activeMode = mode
  const displayRemoteUrl =
    remotePhase === 'processing' ? remoteState.remoteUrl : remoteUrl
  const skippedFiles = remoteState.skippedFiles

  const addFiles = useCallback(
    (files: File[]) => {
      void (async () => {
        const nextItems: SelectedUploadFile[] = []

        for (const file of files) {
          if (isBlockedFile(file.name)) {
            toast.error(t('fileBlocked', { name: file.name }), {
              duration: 2500,
            })
            continue
          }

          if (file.size > MAX_UPLOAD_FILE_SIZE_BYTES) {
            toast.error(
              t('fileTooLarge', {
                name: file.name,
                size: formatFileSize(MAX_UPLOAD_FILE_SIZE_BYTES),
              }),
              { duration: 2500 },
            )
            continue
          }

          nextItems.push({
            id: createLocalFileId(),
            file,
            fileType: await detectFileType(file),
          })
        }

        if (nextItems.length === 0) return

        setSelectedFiles((prev) => {
          const remainingSlots = Math.max(MAX_UPLOAD_FILES - prev.length, 0)
          const kept = nextItems.slice(0, remainingSlots)
          if (nextItems.length > remainingSlots) {
            toast(t('tooManyFiles', { count: MAX_UPLOAD_FILES }), {
              duration: 2500,
            })
          }
          return [...prev, ...kept]
        })
      })()
    },
    [t],
  )

  const removeFile = useCallback((id: string) => {
    setSelectedFiles((prev) => prev.filter((file) => file.id !== id))
  }, [])

  const handleRemoteUrlChange = useCallback(
    (value: string) => {
      if (remotePhase === 'done') {
        resetRemote()
      }
      setRemoteUrl(value)
    },
    [remotePhase, resetRemote],
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: addFiles,
    multiple: true,
  })

  const buildCommonBody = () => {
    const body: Record<string, unknown> = {
      parent: targetUri.trim() || undefined,
      strict,
      telemetry: true,
      wait: false,
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
      if (selectedFiles.length === 0) return
      enqueueUploads({
        files: selectedFiles.map(({ file, fileType }) => ({ file, fileType })),
        commonBody: buildCommonBody(),
      })
      setSelectedFiles([])
      onSubmitted?.()
      return
    }

    const url = remoteUrl.trim()
    if (!url) return
    startRemote({ url, commonBody: buildCommonBody() })
    onSubmitted?.()
  }

  const handleReset = () => {
    resetRemote()
    setSelectedFiles([])
    setRemoteUrl('')
    setMode('upload')
  }

  const canSubmit =
    activeMode === 'upload' ? selectedFiles.length > 0 : !!remoteUrl.trim()

  return (
    <div className="flex flex-col gap-6">
      <div className="space-y-5">
        <div className="flex gap-1 rounded-lg bg-muted p-1">
          <button
            type="button"
            className={`flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              activeMode === 'upload'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
            onClick={() => setMode('upload')}
          >
            <Upload className="size-4" />
            {t('mode.upload')}
          </button>
          <button
            type="button"
            className={`flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              activeMode === 'remote'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
            onClick={() => setMode('remote')}
          >
            <Globe className="size-4" />
            {t('mode.remote')}
          </button>
        </div>

        {activeMode === 'upload' ? (
          <div className="space-y-3">
            <div
              {...getRootProps()}
              className={cn(
                'relative rounded-lg border-2 border-dashed p-8 text-center transition-colors',
                isDragActive
                  ? 'cursor-pointer border-primary bg-primary/5'
                  : 'cursor-pointer border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/30',
              )}
            >
              <input {...getInputProps()} />
              <div className="space-y-2">
                <Upload className="mx-auto size-10 text-muted-foreground/60" />
                <p className="text-sm font-medium">{t('dropzone.title')}</p>
                <p className="text-xs text-muted-foreground">
                  {t('dropzone.hint')}
                </p>
                <p className="text-xs text-muted-foreground/70">
                  {t('dropzone.supportedFormats')}
                </p>
              </div>
            </div>

            {selectedFiles.length > 0 ? (
              <div className="overflow-hidden rounded-lg border border-border/60 bg-muted/10">
                {selectedFiles.map(({ id, file }) => (
                  <div
                    key={id}
                    className="flex items-center gap-3 border-b border-border/50 px-4 py-3 last:border-b-0"
                  >
                    <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted">
                      <FileIcon className="size-5 text-muted-foreground" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {file.name}
                      </p>
                    </div>
                    <div className="shrink-0 text-xs text-muted-foreground">
                      {formatFileSize(file.size)}
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="shrink-0 text-muted-foreground hover:text-foreground"
                      onClick={() => removeFile(id)}
                    >
                      {t('fileInfo.remove')}
                    </Button>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="space-y-2">
            <Label htmlFor="add-resource-remote-url">{t('remoteUrl')}</Label>
            <Input
              id="add-resource-remote-url"
              placeholder={t('remoteUrl.placeholder')}
              value={displayRemoteUrl}
              onChange={(e) => handleRemoteUrlChange(e.target.value)}
              disabled={remotePhase === 'processing'}
            />
            <p className="text-xs text-muted-foreground">
              {t('remoteUrl.hint')}
            </p>
          </div>
        )}

        {activeMode === 'remote' && remotePhase === 'processing' ? (
          <div className="space-y-2 rounded-lg border border-border/50 bg-muted/10 p-4">
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
        ) : null}

        {activeMode === 'remote' && remotePhase === 'done' ? (
          <div className="space-y-3 rounded-lg border border-border/50 bg-muted/10 p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-green-600 dark:text-green-400">
              <CheckCircle2 className="size-4" />
              {t('result.success')}
            </div>

            {skippedFiles.length > 0 ? (
              <Collapsible>
                <CollapsibleTrigger className="flex items-center gap-1 text-sm text-amber-600 hover:underline dark:text-amber-400">
                  <AlertTriangle className="size-4" />
                  {t('result.skippedFiles', { count: skippedFiles.length })}
                  <ChevronRight className="size-3" />
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {skippedFiles.map((file) => (
                      <li key={file} className="truncate">
                        - {file}
                      </li>
                    ))}
                  </ul>
                </CollapsibleContent>
              </Collapsible>
            ) : null}
          </div>
        ) : null}

        <div className="space-y-2">
          <Label htmlFor="add-resource-target">{t('targetUri')}</Label>
          <div className="flex gap-2">
            <Input
              id="add-resource-target"
              placeholder={t('targetUri.placeholder')}
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

        <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
          <CollapsibleTrigger className="flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground">
            <ChevronRight
              className={`size-4 transition-transform ${advancedOpen ? 'rotate-90' : ''}`}
            />
            {t('advancedOptions')}
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="mt-3 space-y-4 rounded-lg border border-border/50 bg-muted/10 p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
                <Label className="flex items-center gap-2">
                  <Checkbox
                    checked={strict}
                    onCheckedChange={(checked) => setStrict(Boolean(checked))}
                  />
                  <span>{t('strict')}</span>
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <Info className="size-3.5 text-muted-foreground" />
                      }
                    />
                    <TooltipContent>{t('strict.hint')}</TooltipContent>
                  </Tooltip>
                </Label>
                <Label className="flex items-center gap-2">
                  <Checkbox
                    checked={directlyUploadMedia}
                    onCheckedChange={(checked) =>
                      setDirectlyUploadMedia(Boolean(checked))
                    }
                  />
                  <span>{t('directlyUploadMedia')}</span>
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <Info className="size-3.5 text-muted-foreground" />
                      }
                    />
                    <TooltipContent>
                      {t('directlyUploadMedia.hint')}
                    </TooltipContent>
                  </Tooltip>
                </Label>
              </div>

              {activeMode === 'remote' ? (
                <div className="space-y-4 border-t border-border/50 pt-4">
                  <div className="space-y-2">
                    <Label htmlFor="add-resource-ignore-dirs">
                      {t('directoryScan.ignoreDirs')}
                    </Label>
                    <Input
                      id="add-resource-ignore-dirs"
                      placeholder={t('directoryScan.ignoreDirs.placeholder')}
                      value={ignoreDirs}
                      onChange={(e) => setIgnoreDirs(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="add-resource-include">
                      {t('directoryScan.include')}
                    </Label>
                    <Input
                      id="add-resource-include"
                      placeholder={t('directoryScan.include.placeholder')}
                      value={include}
                      onChange={(e) => setInclude(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="add-resource-exclude">
                      {t('directoryScan.exclude')}
                    </Label>
                    <Input
                      id="add-resource-exclude"
                      placeholder={t('directoryScan.exclude.placeholder')}
                      value={exclude}
                      onChange={(e) => setExclude(e.target.value)}
                    />
                  </div>
                </div>
              ) : null}

              <div className="space-y-2">
                <Label htmlFor="add-resource-reason">{t('reason')}</Label>
                <Textarea
                  id="add-resource-reason"
                  placeholder={t('reason.placeholder')}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="add-resource-instruction">
                  {t('instruction')}
                </Label>
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

        <Button
          onClick={handleSubmit}
          disabled={
            !canSubmit ||
            (activeMode === 'remote' && remotePhase === 'processing')
          }
        >
          {activeMode === 'remote' && remotePhase === 'processing'
            ? t('uploading')
            : t('startProcessing')}
        </Button>
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
