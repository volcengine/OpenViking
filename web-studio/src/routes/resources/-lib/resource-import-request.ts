import type { AdditionalResourceOptionsValue } from '../-components/additional-resource-options'
import type { ResourceDestinationMode } from '../-components/resource-destination-fields'
import { parseResourceTags } from './resource-option-values'
import type { RemoteResourceKind } from './resource-source'
import {
  buildRemoteSourceRequestOptions,
  getRemoteResourceCapabilities,
} from './resource-source-strategy'
import type { RemoteSourceOptionState } from './resource-source-strategy'
import type {
  ResourceImportCommonBody,
  ResourceImportRequest,
} from './resource-import-types'

export type ResourceImportFormState = {
  additionalOptions: AdditionalResourceOptionsValue
  createParent: boolean
  destinationMode: ResourceDestinationMode
  directlyUploadMedia: boolean
  exclude: string
  ignoreDirs: string
  include: string
  instruction: string
  mode: 'remote' | 'upload'
  reason: string
  remoteResourceKind: RemoteResourceKind
  sourceOptionState: RemoteSourceOptionState
  strict: boolean
  targetUri: string
  watchEnabled: boolean
  watchInterval: string
}

export function buildResourceImportCommonBody({
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
  watchEnabled,
  watchInterval,
}: ResourceImportFormState): ResourceImportCommonBody {
  const sourceCapabilities = getRemoteResourceCapabilities(
    mode === 'remote' ? remoteResourceKind : 'unknown',
  )
  const effectiveDestinationMode: ResourceDestinationMode =
    sourceCapabilities.exactDestination ? 'to' : destinationMode
  const tags = parseResourceTags(additionalOptions.tags)
  const body: ResourceImportCommonBody = {
    [effectiveDestinationMode]: targetUri.trim() || undefined,
    strict: sourceCapabilities.nativeOptions ? strict : false,
    create_parent: createParent,
    telemetry: true,
    wait: sourceCapabilities.nativeOptions ? additionalOptions.wait : false,
    ...(sourceCapabilities.nativeOptions &&
    additionalOptions.wait &&
    additionalOptions.timeout.trim()
      ? { timeout: Number(additionalOptions.timeout) }
      : {}),
    directly_upload_media: sourceCapabilities.nativeOptions
      ? directlyUploadMedia
      : true,
    ...(sourceCapabilities.nativeOptions &&
    additionalOptions.preserveStructure !== undefined
      ? { preserve_structure: additionalOptions.preserveStructure }
      : {}),
    processing_mode: sourceCapabilities.nativeOptions
      ? additionalOptions.processingMode
      : 'semantic_and_vectors',
    ...(tags.length ? { tags, tag_mode: additionalOptions.tagMode } : {}),
    ...(mode === 'remote' &&
    sourceCapabilities.nativeOptions &&
    additionalOptions.sourceName.trim()
      ? { source_name: additionalOptions.sourceName.trim() }
      : {}),
    ...(sourceCapabilities.nativeOptions &&
    additionalOptions.parseMode === 'no_split'
      ? { args: { parse_mode: 'no_split' } }
      : {}),
  }

  if (reason.trim()) body.reason = reason.trim()
  if (instruction.trim() && sourceCapabilities.nativeOptions) {
    body.instruction = instruction.trim()
  }
  if (mode !== 'remote') return body

  if (watchEnabled && sourceCapabilities.watch) {
    body.watch_interval = Number(watchInterval)
  }
  if (ignoreDirs.trim() && sourceCapabilities.nativeOptions) {
    body.ignore_dirs = ignoreDirs.trim()
  }
  if (include.trim() && sourceCapabilities.nativeOptions) {
    body.include = include.trim()
  }
  if (exclude.trim() && sourceCapabilities.nativeOptions) {
    body.exclude = exclude.trim()
  }

  const sourceRequestOptions = buildRemoteSourceRequestOptions(
    remoteResourceKind,
    sourceOptionState,
  )
  const { args: sourceArgs, ...sourceOptions } = sourceRequestOptions
  Object.assign(body, sourceOptions)
  const mergedArgs = { ...body.args, ...sourceArgs }
  if (Object.keys(mergedArgs).length > 0) body.args = mergedArgs
  else delete body.args

  return body
}

export function buildRemoteResourceRequest(
  url: string,
  commonBody: ResourceImportCommonBody,
): ResourceImportRequest {
  return {
    ...commonBody,
    path: url.trim(),
  }
}

export function buildUploadedResourceRequest(
  tempFileId: string,
  sourceName: string,
  commonBody: ResourceImportCommonBody,
): ResourceImportRequest {
  const {
    add_type: _addType,
    watch_interval: _watchInterval,
    ...uploadBody
  } = commonBody

  return {
    ...uploadBody,
    source_name: sourceName,
    temp_file_id: tempFileId.trim(),
  }
}
