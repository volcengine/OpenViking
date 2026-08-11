import { fileTypeFromBlob } from 'file-type'
import { FileIcon, Upload } from 'lucide-react'
import type { TFunction } from 'i18next'
import { useCallback, useRef } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import { useDropzone } from 'react-dropzone'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import { cn } from '#/lib/utils'
import {
  MAX_UPLOAD_FILES,
  MAX_UPLOAD_FILE_SIZE_BYTES,
  formatFileSize,
  isBlockedFile,
} from '../-lib/upload'

export type SelectedUploadFile = {
  id: string
  file: File
  fileType: string | null
}

type UploadResourceFieldsProps = {
  files: SelectedUploadFile[]
  onFilesChange: Dispatch<SetStateAction<SelectedUploadFile[]>>
  t: TFunction<'addResource'>
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

export function UploadResourceFields({
  files,
  onFilesChange,
  t,
}: UploadResourceFieldsProps) {
  const filesRef = useRef(files)
  filesRef.current = files

  const updateFiles = useCallback(
    (update: (currentFiles: SelectedUploadFile[]) => SelectedUploadFile[]) => {
      const nextFiles = update(filesRef.current)
      filesRef.current = nextFiles
      onFilesChange(nextFiles)
    },
    [onFilesChange],
  )

  const addFiles = useCallback(
    (nextFiles: File[]) => {
      void (async () => {
        const accepted: SelectedUploadFile[] = []

        for (const file of nextFiles) {
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
          accepted.push({
            id: createLocalFileId(),
            file,
            fileType: await detectFileType(file),
          })
        }

        updateFiles((currentFiles) => {
          const remainingSlots = Math.max(
            MAX_UPLOAD_FILES - currentFiles.length,
            0,
          )
          if (accepted.length > remainingSlots) {
            toast(t('tooManyFiles', { count: MAX_UPLOAD_FILES }), {
              duration: 2500,
            })
          }
          return [...currentFiles, ...accepted.slice(0, remainingSlots)]
        })
      })()
    },
    [t, updateFiles],
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: addFiles,
    multiple: true,
  })

  return (
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
          <p className="text-xs text-muted-foreground">{t('dropzone.hint')}</p>
          <p className="text-xs text-muted-foreground/70">
            {t('dropzone.supportedFormats')}
          </p>
        </div>
      </div>

      {files.length ? (
        <div className="overflow-hidden rounded-lg border border-border/60 bg-muted/10">
          {files.map(({ id, file }) => (
            <div
              key={id}
              className="flex items-center gap-3 border-b border-border/50 px-4 py-3 last:border-b-0"
            >
              <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted">
                <FileIcon className="size-5 text-muted-foreground" />
              </div>
              <p className="min-w-0 flex-1 truncate text-sm font-medium">
                {file.name}
              </p>
              <span className="shrink-0 text-xs text-muted-foreground">
                {formatFileSize(file.size)}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="shrink-0 text-muted-foreground hover:text-foreground"
                onClick={() =>
                  updateFiles((currentFiles) =>
                    currentFiles.filter((item) => item.id !== id),
                  )
                }
              >
                {t('fileInfo.remove')}
              </Button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}
