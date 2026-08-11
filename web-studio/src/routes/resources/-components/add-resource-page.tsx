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
import { parsePositiveMinutes } from '#/lib/watch-interval'
import { useResourceUpload } from '../-hooks/use-resource-upload'
import type { RemoteStartResult } from '../-hooks/use-resource-upload'
import {
  detectRemoteResourceKind,
  matchesRemoteResourceTypeSelection,
} from '../-lib/resource-source'
import type { RemoteResourceTypeSelection } from '../-lib/resource-source'
import {
  isOptionalIntegerValid,
  parseDelimitedValues,
  parseOptionalInteger,
  parseResourceTags,
} from '../-lib/resource-option-values'
import { DirectoryPickerDialog } from './directory-picker-dialog'
import { AdditionalResourceOptions } from './additional-resource-options'
import type { AdditionalResourceOptionsValue } from './additional-resource-options'
import { FeishuResourceOptions } from './feishu-resource-options'
import type { FeishuAuthMode } from './feishu-resource-options'
import { GitResourceOptions } from './git-resource-options'
import type { GitAuthMode, GitRefMode } from './git-resource-options'
import { RemoteResourceFields } from './remote-resource-fields'
import { ResourceDestinationFields } from './resource-destination-fields'
import type { ResourceDestinationMode } from './resource-destination-fields'
import { TosResourceOptions } from './tos-resource-options'
import { UploadResourceFields } from './upload-resource-fields'
import type { SelectedUploadFile } from './upload-resource-fields'
import { WebResourceOptions } from './web-resource-options'
import type { WebResourceOptionsValue } from './web-resource-options'
import type { AddResourceCommonBody } from '@ov-server/api/v1/resources'

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

const DEFAULT_ADDITIONAL_OPTIONS: AdditionalResourceOptionsValue = {
  parseMode: 'default',
  preserveStructure: true,
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
}: {
  initialMode?: Mode
  initialWatchEnabled?: boolean
  onAccepted?: (result: RemoteStartResult) => void
  onCompleted?: () => void
  onFailed?: () => void
  onSubmitted?: () => void
} = {}) {
  const { i18n, t } = useTranslation('addResource')
  const { enqueueUploads, startRemote, resetRemote, remoteState } =
    useResourceUpload()

  const [mode, setMode] = useState<Mode>(initialMode)
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
  const [feishuAuthMode, setFeishuAuthMode] = useState<FeishuAuthMode>('app')
  const [feishuAccessToken, setFeishuAccessToken] = useState('')
  const [feishuRefreshToken, setFeishuRefreshToken] = useState('')
  const [gitRefMode, setGitRefMode] = useState<GitRefMode>('branch')
  const [gitRef, setGitRef] = useState('')
  const [gitAuthMode, setGitAuthMode] = useState<GitAuthMode>('public')
  const [gitUsername, setGitUsername] = useState('oauth2')
  const [gitToken, setGitToken] = useState('')
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
  const remoteResourceTypeMatches = matchesRemoteResourceTypeSelection(
    detectedRemoteResourceKind,
    remoteResourceType,
  )
  const isTosResource = remoteResourceKind === 'tos'
  const effectiveDestinationMode: ResourceDestinationMode = isTosResource
    ? 'to'
    : destinationMode

  const handleRemoteUrlChange = useCallback(
    (value: string) => {
      if (remotePhase === 'done') {
        resetRemote()
      }
      setRemoteUrl(value)
    },
    [remotePhase, resetRemote],
  )

  const resetRemoteSourceFields = useCallback(() => {
    setWatchEnabled(initialWatchEnabled)
    setWatchInterval('1440')
    setFeishuAuthMode('app')
    setFeishuAccessToken('')
    setFeishuRefreshToken('')
    setGitRefMode('branch')
    setGitRef('')
    setGitAuthMode('public')
    setGitUsername('oauth2')
    setGitToken('')
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

  const buildCommonBody = () => {
    const parsedTags = parseResourceTags(additionalOptions.tags)
    const timeout = Number(additionalOptions.timeout)
    const body: AddResourceCommonBody = {
      [effectiveDestinationMode]: targetUri.trim() || undefined,
      strict: isTosResource ? false : strict,
      create_parent: createParent,
      telemetry: true,
      wait: isTosResource ? false : additionalOptions.wait,
      ...(!isTosResource &&
      additionalOptions.wait &&
      additionalOptions.timeout.trim()
        ? { timeout }
        : {}),
      directly_upload_media: isTosResource ? true : directlyUploadMedia,
      preserve_structure: isTosResource
        ? true
        : additionalOptions.preserveStructure,
      processing_mode: isTosResource
        ? 'semantic_and_vectors'
        : additionalOptions.processingMode,
      ...(parsedTags.tags.length
        ? { tags: parsedTags.tags, tag_mode: additionalOptions.tagMode }
        : {}),
      ...(mode === 'remote' &&
      !isTosResource &&
      additionalOptions.sourceName.trim()
        ? { source_name: additionalOptions.sourceName.trim() }
        : {}),
      ...(!isTosResource && additionalOptions.parseMode === 'no_split'
        ? { args: { parse_mode: 'no_split' } }
        : {}),
    }
    if (reason.trim()) {
      body.reason = reason.trim()
    }
    if (instruction.trim() && !isTosResource) {
      body.instruction = instruction.trim()
    }
    if (mode === 'remote') {
      if (watchEnabled && !isTosResource) {
        const minutes = parsePositiveMinutes(watchInterval)
        if (minutes !== null) body.watch_interval = minutes
      }
      if (ignoreDirs.trim() && !isTosResource) {
        body.ignore_dirs = ignoreDirs.trim()
      }
      if (include.trim() && !isTosResource) {
        body.include = include.trim()
      }
      if (exclude.trim() && !isTosResource) {
        body.exclude = exclude.trim()
      }
      if (remoteResourceKind === 'feishu' && feishuAuthMode === 'user') {
        body.args = {
          ...body.args,
          feishu_access_token: feishuAccessToken.trim(),
          ...(watchEnabled
            ? { feishu_refresh_token: feishuRefreshToken.trim() }
            : {}),
        }
      }
      if (remoteResourceKind === 'git') {
        body.args = {
          ...body.args,
          ...(gitRef.trim() ? { [gitRefMode]: gitRef.trim() } : {}),
          ...(gitAuthMode === 'token'
            ? {
                auth_config: {
                  username: gitUsername.trim() || 'oauth2',
                  token: gitToken.trim(),
                },
              }
            : {}),
        }
      }
      if (
        remoteResourceKind === 'webPage' ||
        remoteResourceKind === 'webFeed'
      ) {
        const includePaths = parseDelimitedValues(webOptions.includePaths)
        const excludePaths = parseDelimitedValues(webOptions.excludePaths)
        const isRecursiveWebImport = webOptions.mode === 'recursive'
        const isSiteImport = webOptions.mode === 'site'
        body.args = {
          ...body.args,
          ...(webOptions.mode === 'single' ? { site: false, depth: 0 } : {}),
          ...(isRecursiveWebImport
            ? {
                site: false,
                depth: parseOptionalInteger(webOptions.depth, 0),
                max_pages: parseOptionalInteger(webOptions.maxPages, 1),
                include_paths: includePaths.length ? includePaths : undefined,
                exclude_paths: excludePaths.length ? excludePaths : undefined,
                allow_external_links: webOptions.allowExternalLinks,
                skip_download_links: webOptions.skipDownloadLinks,
              }
            : {}),
          ...(isSiteImport
            ? {
                site: true,
                max_pages: parseOptionalInteger(webOptions.maxPages, 1),
              }
            : {}),
        }
        if (isSiteImport && webOptions.includePaths.trim()) {
          body.include = webOptions.includePaths.trim()
        }
        if (isSiteImport && webOptions.excludePaths.trim()) {
          body.exclude = webOptions.excludePaths.trim()
        }
      }
      if (isTosResource) {
        body.add_type = 'tos'
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
    setMode(initialMode)
    resetRemoteSourceFields()
    setAdditionalOptions(DEFAULT_ADDITIONAL_OPTIONS)
    setDestinationMode('parent')
  }

  const hasValidWatchInterval =
    !watchEnabled || parsePositiveMinutes(watchInterval) !== null
  const hasValidWebOptions =
    !['webPage', 'webFeed'].includes(remoteResourceKind) ||
    ((!['recursive'].includes(webOptions.mode) ||
      isOptionalIntegerValid(webOptions.depth, 0)) &&
      (!['recursive', 'site'].includes(webOptions.mode) ||
        isOptionalIntegerValid(webOptions.maxPages, 1)))
  const parsedTags = parseResourceTags(additionalOptions.tags)
  const hasValidTimeout =
    isTosResource ||
    !additionalOptions.wait ||
    !additionalOptions.timeout.trim() ||
    (Number.isFinite(Number(additionalOptions.timeout)) &&
      Number(additionalOptions.timeout) > 0)
  const hasValidCommonOptions = parsedTags.valid && hasValidTimeout
  const hasValidTosOptions =
    !isTosResource || (effectiveDestinationMode === 'to' && !!targetUri.trim())
  const canSubmit =
    hasValidCommonOptions &&
    hasValidTosOptions &&
    remoteResourceTypeMatches &&
    (activeMode === 'upload'
      ? selectedFiles.length > 0
      : !!remoteUrl.trim() &&
        (isTosResource || hasValidWatchInterval) &&
        hasValidWebOptions &&
        (remoteResourceKind !== 'feishu' ||
          feishuAuthMode === 'app' ||
          (!!feishuAccessToken.trim() &&
            (!watchEnabled || !!feishuRefreshToken.trim()))) &&
        (remoteResourceKind !== 'git' ||
          gitAuthMode === 'public' ||
          !!gitToken.trim()))

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
            resourceTypeMatches={remoteResourceTypeMatches}
            t={t}
            url={displayRemoteUrl}
            watchEnabled={watchEnabled}
            watchInterval={watchInterval}
            watchSupported={!isTosResource}
          >
            {remoteResourceKind === 'feishu' ? (
              <FeishuResourceOptions
                accessToken={feishuAccessToken}
                authMode={feishuAuthMode}
                disabled={remotePhase === 'processing'}
                documentationUrl={feishuConfigurationUrl}
                onAccessTokenChange={setFeishuAccessToken}
                onAuthModeChange={setFeishuAuthMode}
                onRefreshTokenChange={setFeishuRefreshToken}
                refreshToken={feishuRefreshToken}
                t={t}
                watchEnabled={watchEnabled}
              />
            ) : null}
            {remoteResourceKind === 'git' ? (
              <GitResourceOptions
                authMode={gitAuthMode}
                disabled={remotePhase === 'processing'}
                onAuthModeChange={setGitAuthMode}
                onRefChange={setGitRef}
                onRefModeChange={setGitRefMode}
                onTokenChange={setGitToken}
                onUsernameChange={setGitUsername}
                refMode={gitRefMode}
                refValue={gitRef}
                supportsHttpAuth={remoteUrl.trim().startsWith('https://')}
                t={t}
                token={gitToken}
                username={gitUsername}
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
          exactOnly={isTosResource}
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
                    checked={isTosResource ? false : strict}
                    disabled={isTosResource}
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
                    checked={isTosResource ? true : directlyUploadMedia}
                    disabled={isTosResource}
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
                tosMode={isTosResource}
                disabled={remotePhase === 'processing'}
                isRemote={activeMode === 'remote'}
                onChange={setAdditionalOptions}
                t={t}
                tagsValid={parsedTags.valid}
                value={additionalOptions}
              />

              {activeMode === 'remote' && !isTosResource ? (
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

              {!isTosResource ? (
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
