import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Globe,
  Info,
  Loader2Icon,
  Upload,
} from 'lucide-react'
import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'

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
import { useResourceUpload } from '../-hooks/use-resource-upload'
import type { RemoteStartResult } from '../-hooks/use-resource-upload'
import { detectRemoteResourceKind } from '../-lib/resource-source'
import type { RemoteResourceTypeSelection } from '../-lib/resource-source'
import { buildResourceImportCommonBody } from '../-lib/resource-import-request'
import { getRemoteResourceCapabilities } from '../-lib/resource-source-strategy'
import type { RemoteSourceOptionState } from '../-lib/resource-source-strategy'
import { DirectoryPickerDialog } from './directory-picker-dialog'
import { AdditionalResourceOptions } from './additional-resource-options'
import type { AdditionalResourceOptionsValue } from './additional-resource-options'
import { FeishuResourceOptions } from './feishu-resource-options'
import type { FeishuResourceOptionsValue } from './feishu-resource-options'
import { GitResourceOptions } from './git-resource-options'
import type { GitResourceOptionsValue } from './git-resource-options'
import { RemoteResourceFields } from './remote-resource-fields'
import { ResourceDestinationFields } from './resource-destination-fields'
import type { ResourceDestinationMode } from './resource-destination-fields'
import { TosResourceOptions } from './tos-resource-options'
import { UploadResourceFields } from './upload-resource-fields'
import type { SelectedUploadFile } from './upload-resource-fields'
import { WebResourceOptions } from './web-resource-options'
import type { WebResourceOptionsValue } from './web-resource-options'

type Mode = 'upload' | 'remote'

const DEFAULT_WEB_OPTIONS: WebResourceOptionsValue = {
  allowExternalLinks: false,
  depth: '1',
  excludePaths: '',
  includePaths: '',
  maxPages: '50',
  mode: 'auto',
  skipDownloadLinks: true,
}

const DEFAULT_FEISHU_OPTIONS: FeishuResourceOptionsValue = {
  accessToken: '',
  authMode: 'app',
  refreshToken: '',
}

const DEFAULT_GIT_OPTIONS: GitResourceOptionsValue = {
  authMode: 'public',
  refMode: 'branch',
  refValue: '',
  token: '',
  username: 'oauth2',
}

const DEFAULT_ADDITIONAL_OPTIONS: AdditionalResourceOptionsValue = {
  parseMode: 'default',
  processingMode: 'semantic_and_vectors',
  sourceName: '',
  tagMode: 'replace',
  tags: '',
  timeout: '',
  wait: false,
}

export function AddResourceForm({
  initialMode = 'upload',
  initialWatchEnabled = false,
  onAccepted,
  onCompleted,
  onFailed,
  onSubmitted,
  watchRequired = false,
}: {
  initialMode?: Mode
  initialWatchEnabled?: boolean
  onAccepted?: (result: RemoteStartResult) => void
  onCompleted?: () => void
  onFailed?: () => void
  onSubmitted?: () => void
  watchRequired?: boolean
} = {}) {
  const { i18n, t } = useTranslation('addResource')
  const { enqueueUploads, startRemote, resetRemote, remoteState } =
    useResourceUpload()

  const [mode, setMode] = useState<Mode>(watchRequired ? 'remote' : initialMode)
  const [remoteUrl, setRemoteUrl] = useState('')
  const [remoteResourceType, setRemoteResourceType] =
    useState<RemoteResourceTypeSelection>('auto')
  const [selectedFiles, setSelectedFiles] = useState<SelectedUploadFile[]>([])
  const [targetUri, setTargetUri] = useState('viking://resources/')
  const [destinationMode, setDestinationMode] =
    useState<ResourceDestinationMode>('parent')
  const [strict, setStrict] = useState(false)
  const [createParent, setCreateParent] = useState(true)
  const [directlyUploadMedia, setDirectlyUploadMedia] = useState(true)
  const [reason, setReason] = useState('')
  const [instruction, setInstruction] = useState('')
  const [ignoreDirs, setIgnoreDirs] = useState('')
  const [include, setInclude] = useState('')
  const [exclude, setExclude] = useState('')
  const [watchEnabled, setWatchEnabled] = useState(initialWatchEnabled)
  const [watchInterval, setWatchInterval] = useState('1440')
  const [feishuOptions, setFeishuOptions] = useState(DEFAULT_FEISHU_OPTIONS)
  const [gitOptions, setGitOptions] = useState(DEFAULT_GIT_OPTIONS)
  const [webOptions, setWebOptions] =
    useState<WebResourceOptionsValue>(DEFAULT_WEB_OPTIONS)
  const [additionalOptions, setAdditionalOptions] =
    useState<AdditionalResourceOptionsValue>(DEFAULT_ADDITIONAL_OPTIONS)
  const feishuConfigurationUrl = i18n.resolvedLanguage?.startsWith('zh')
    ? 'https://docs.openviking.ai/zh/guides/01-configuration#feishu'
    : 'https://docs.openviking.ai/en/guides/01-configuration#feishu'
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [dirPickerOpen, setDirPickerOpen] = useState(false)

  const remotePhase = remoteState.phase
  const activeMode = mode
  const displayRemoteUrl =
    remotePhase === 'processing' ? remoteState.remoteUrl : remoteUrl
  const skippedFiles = remoteState.skippedFiles
  const detectedRemoteResourceKind = detectRemoteResourceKind(remoteUrl)
  const remoteResourceKind =
    remoteResourceType === 'auto'
      ? detectedRemoteResourceKind
      : remoteResourceType
  const sourceCapabilities = getRemoteResourceCapabilities(
    activeMode === 'remote' ? remoteResourceKind : 'unknown',
  )
  const gitSupportsHttpAuth = remoteUrl
    .trim()
    .toLowerCase()
    .startsWith('https://')
  const effectiveWatchEnabled = watchEnabled && sourceCapabilities.watch
  const effectiveDestinationMode: ResourceDestinationMode =
    sourceCapabilities.exactDestination ? 'to' : destinationMode
  const sourceOptionState: RemoteSourceOptionState = {
    feishu: feishuOptions,
    git: {
      ...gitOptions,
      supportsHttpAuth: gitSupportsHttpAuth,
    },
    watchEnabled: effectiveWatchEnabled,
    web: webOptions,
  }

  const handleRemoteUrlChange = useCallback(
    (value: string) => {
      if (remotePhase === 'done') {
        resetRemote()
      }
      const nextSupportsHttpAuth = value
        .trim()
        .toLowerCase()
        .startsWith('https://')
      if (gitSupportsHttpAuth && !nextSupportsHttpAuth) {
        setGitOptions((current) => ({
          ...current,
          authMode: 'public',
          token: '',
        }))
      }
      setRemoteUrl(value)
    },
    [gitSupportsHttpAuth, remotePhase, resetRemote],
  )

  const resetRemoteSourceFields = useCallback(() => {
    setWatchEnabled(initialWatchEnabled)
    setWatchInterval('1440')
    setFeishuOptions(DEFAULT_FEISHU_OPTIONS)
    setGitOptions(DEFAULT_GIT_OPTIONS)
    setWebOptions(DEFAULT_WEB_OPTIONS)
  }, [initialWatchEnabled])

  const handleRemoteResourceTypeChange = useCallback(
    (value: RemoteResourceTypeSelection) => {
      resetRemote()
      setRemoteUrl('')
      setRemoteResourceType(value)
      resetRemoteSourceFields()
    },
    [resetRemote, resetRemoteSourceFields],
  )

  const buildCommonBody = () =>
    buildResourceImportCommonBody({
      additionalOptions,
      createParent,
      destinationMode,
      directlyUploadMedia,
      exclude,
      ignoreDirs,
      include,
      instruction,
      mode,
      reason,
      remoteResourceKind,
      sourceOptionState,
      strict,
      targetUri,
      watchEnabled: effectiveWatchEnabled,
      watchInterval,
    })

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
    startRemote({
      url,
      commonBody: buildCommonBody(),
      onAccepted,
      onCompleted,
      onFailed,
    })
    onSubmitted?.()
  }

  const handleReset = () => {
    resetRemote()
    setSelectedFiles([])
    setRemoteUrl('')
    setRemoteResourceType('auto')
    setMode(watchRequired ? 'remote' : initialMode)
    resetRemoteSourceFields()
    setAdditionalOptions(DEFAULT_ADDITIONAL_OPTIONS)
    setDestinationMode('parent')
  }

  const hasWatchInterval =
    !effectiveWatchEnabled || Boolean(watchInterval.trim())
  const hasValidDestination =
    !sourceCapabilities.exactDestination ||
    (effectiveDestinationMode === 'to' && !!targetUri.trim())
  const watchRequirementMet =
    !watchRequired ||
    (activeMode === 'remote' &&
      sourceCapabilities.watch &&
      effectiveWatchEnabled &&
      hasWatchInterval)
  const canSubmit =
    hasValidDestination &&
    watchRequirementMet &&
    (activeMode === 'upload'
      ? selectedFiles.length > 0
      : !!remoteUrl.trim() && hasWatchInterval)

  return (
    <div className="flex flex-col gap-6">
      <div className="space-y-5">
        {!watchRequired ? (
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
        ) : null}

        {activeMode === 'upload' ? (
          <UploadResourceFields
            files={selectedFiles}
            onFilesChange={setSelectedFiles}
            t={t}
          />
        ) : (
          <RemoteResourceFields
            disabled={remotePhase === 'processing'}
            onResourceTypeChange={handleRemoteResourceTypeChange}
            onUrlChange={handleRemoteUrlChange}
            onWatchEnabledChange={setWatchEnabled}
            onWatchIntervalChange={setWatchInterval}
            resourceKind={remoteResourceKind}
            resourceType={remoteResourceType}
            t={t}
            url={displayRemoteUrl}
            watchEnabled={effectiveWatchEnabled}
            watchInterval={watchInterval}
            watchRequired={watchRequired}
            watchSupported={sourceCapabilities.watch}
          >
            {remoteResourceKind === 'feishu' ? (
              <FeishuResourceOptions
                disabled={remotePhase === 'processing'}
                documentationUrl={feishuConfigurationUrl}
                onChange={setFeishuOptions}
                t={t}
                value={feishuOptions}
                watchEnabled={effectiveWatchEnabled}
              />
            ) : null}
            {remoteResourceKind === 'git' ? (
              <GitResourceOptions
                disabled={remotePhase === 'processing'}
                onChange={setGitOptions}
                supportsHttpAuth={gitSupportsHttpAuth}
                t={t}
                value={gitOptions}
              />
            ) : null}
            {remoteResourceKind === 'webPage' ||
            remoteResourceKind === 'webFeed' ? (
              <WebResourceOptions
                disabled={remotePhase === 'processing'}
                onChange={setWebOptions}
                t={t}
                value={webOptions}
              />
            ) : null}
            {remoteResourceKind === 'tos' ? <TosResourceOptions t={t} /> : null}
          </RemoteResourceFields>
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

        <ResourceDestinationFields
          disabled={remotePhase === 'processing'}
          exactOnly={sourceCapabilities.exactDestination}
          mode={effectiveDestinationMode}
          onBrowse={() => setDirPickerOpen(true)}
          onModeChange={setDestinationMode}
          onUriChange={setTargetUri}
          t={t}
          uri={targetUri}
        />

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
                    checked={sourceCapabilities.nativeOptions ? strict : false}
                    disabled={!sourceCapabilities.nativeOptions}
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
                    checked={createParent}
                    onCheckedChange={(checked) =>
                      setCreateParent(Boolean(checked))
                    }
                  />
                  <span>{t('createParent')}</span>
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <Info className="size-3.5 text-muted-foreground" />
                      }
                    />
                    <TooltipContent>{t('createParent.hint')}</TooltipContent>
                  </Tooltip>
                </Label>
                <Label className="flex items-center gap-2">
                  <Checkbox
                    checked={
                      sourceCapabilities.nativeOptions
                        ? directlyUploadMedia
                        : true
                    }
                    disabled={!sourceCapabilities.nativeOptions}
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

              <AdditionalResourceOptions
                disabled={remotePhase === 'processing'}
                isRemote={activeMode === 'remote'}
                nativeOptions={sourceCapabilities.nativeOptions}
                onChange={setAdditionalOptions}
                t={t}
                value={additionalOptions}
              />

              {activeMode === 'remote' && sourceCapabilities.nativeOptions ? (
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

              {sourceCapabilities.nativeOptions ? (
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
              ) : null}
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
