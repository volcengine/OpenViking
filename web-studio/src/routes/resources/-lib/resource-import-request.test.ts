import { describe, expect, it } from 'vitest'

import {
  buildRemoteResourceRequest,
  buildUploadedResourceRequest,
} from './resource-import-request'

describe('resource import request builders', () => {
  it('preserves source-specific remote options', () => {
    expect(
      buildRemoteResourceRequest(' https://example.feishu.cn/docx/doc ', {
        args: {
          feishu_access_token: 'u-token',
          feishu_refresh_token: 'r-token',
        },
        processing_mode: 'vectors_only',
        tags: ['team=docs'],
        tag_mode: 'append',
        to: 'viking://resources/feishu/doc',
        watch_interval: 1440,
      }),
    ).toEqual({
      args: {
        feishu_access_token: 'u-token',
        feishu_refresh_token: 'r-token',
      },
      path: 'https://example.feishu.cn/docx/doc',
      processing_mode: 'vectors_only',
      tags: ['team=docs'],
      tag_mode: 'append',
      to: 'viking://resources/feishu/doc',
      watch_interval: 1440,
    })
  })

  it('removes remote-only scheduling and routing fields from uploads', () => {
    expect(
      buildUploadedResourceRequest(' temp-id ', 'guide.pdf', {
        add_type: 'tos',
        args: { parse_mode: 'no_split' },
        parent: 'viking://resources/docs',
        watch_interval: 60,
      }),
    ).toEqual({
      args: { parse_mode: 'no_split' },
      parent: 'viking://resources/docs',
      source_name: 'guide.pdf',
      temp_file_id: 'temp-id',
    })
  })
})
