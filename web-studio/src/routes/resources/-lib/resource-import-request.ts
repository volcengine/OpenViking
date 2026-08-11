import type {
  AddResourceCommonBody,
  AddResourceRequest,
} from '@ov-server/api/v1/resources'

export function buildRemoteResourceRequest(
  url: string,
  commonBody: AddResourceCommonBody,
): AddResourceRequest {
  return {
    ...commonBody,
    path: url.trim(),
  }
}

export function buildUploadedResourceRequest(
  tempFileId: string,
  sourceName: string,
  commonBody: AddResourceCommonBody,
): AddResourceRequest {
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
