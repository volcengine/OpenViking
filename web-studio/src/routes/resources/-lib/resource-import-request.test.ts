import { describe, expect, it } from 'vitest'

import {
  buildResourceImportCommonBody,
  buildRemoteResourceRequest,
  buildUploadedResourceRequest,
} from './resource-import-request'
import type { ResourceImportFormState } from './resource-import-request'

const BASE_FORM_STATE: ResourceImportFormState = {
  additionalOptions: {
    parseMode: 'default',
    processingMode: 'semantic_and_vectors',
    sourceName: '',
    tagMode: 'replace',
    tags: '',
    timeout: '',
    wait: false,
  },
  createParent: true,
  destinationMode: 'parent',
  directlyUploadMedia: true,
  exclude: '',
  ignoreDirs: '',
  include: '',
  instruction: '',
  mode: 'remote',
  reason: '',
  remoteResourceKind: 'unknown',
  sourceOptionState: {
    feishu: { accessToken: '', authMode: 'app', refreshToken: '' },
    git: {
      authMode: 'public',
      refMode: 'branch',
      refValue: '',
      supportsHttpAuth: true,
      token: '',
      username: 'oauth2',
    },
    watchEnabled: false,
    web: {
      allowExternalLinks: false,
      depth: '1',
      excludePaths: '',
      includePaths: '',
      maxPages: '50',
      mode: 'auto',
      skipDownloadLinks: true,
    },
  },
  strict: false,
  targetUri: 'viking://resources/',
  watchEnabled: false,
  watchInterval: '1440',
}

describe('resource import request builders', () => {
  it('serializes native form options in one place', () => {
    expect(
      buildResourceImportCommonBody({
        ...BASE_FORM_STATE,
        additionalOptions: {
          ...BASE_FORM_STATE.additionalOptions,
          parseMode: 'no_split',
          preserveStructure: true,
          processingMode: 'vectors_only',
          sourceName: 'docs',
          tagMode: 'append',
          tags: 'team=docs, env=test',
          timeout: '12.5',
          wait: true,
        },
        exclude: '**/dist/**',
        ignoreDirs: '.git,node_modules',
        include: '**/*.md',
        instruction: ' Prefer API docs. ',
        reason: ' Knowledge base ',
        strict: true,
        watchEnabled: true,
        watchInterval: '60',
      }),
    ).toEqual({
      args: { parse_mode: 'no_split' },
      create_parent: true,
      directly_upload_media: true,
      exclude: '**/dist/**',
      ignore_dirs: '.git,node_modules',
      include: '**/*.md',
      instruction: 'Prefer API docs.',
      parent: 'viking://resources/',
      preserve_structure: true,
      processing_mode: 'vectors_only',
      reason: 'Knowledge base',
      source_name: 'docs',
      strict: true,
      tag_mode: 'append',
      tags: ['team=docs', 'env=test'],
      telemetry: true,
      timeout: 12.5,
      wait: true,
      watch_interval: 60,
    })
  })

  it('merges parse mode with source-specific Git options', () => {
    const body = buildResourceImportCommonBody({
      ...BASE_FORM_STATE,
      additionalOptions: {
        ...BASE_FORM_STATE.additionalOptions,
        parseMode: 'no_split',
      },
      remoteResourceKind: 'git',
      sourceOptionState: {
        ...BASE_FORM_STATE.sourceOptionState,
        git: {
          ...BASE_FORM_STATE.sourceOptionState.git,
          refValue: 'main',
        },
      },
    })

    expect(body.args).toEqual({
      branch: 'main',
      parse_mode: 'no_split',
    })
  })

  it('keeps TOS selectable but emits only its supported request fields', () => {
    expect(
      buildResourceImportCommonBody({
        ...BASE_FORM_STATE,
        additionalOptions: {
          ...BASE_FORM_STATE.additionalOptions,
          parseMode: 'no_split',
          processingMode: 'vectors_only',
          wait: true,
        },
        destinationMode: 'parent',
        instruction: 'ignored',
        remoteResourceKind: 'tos',
        strict: true,
        targetUri: ' viking://resources/tos-item ',
        watchEnabled: true,
      }),
    ).toEqual({
      add_type: 'tos',
      create_parent: true,
      directly_upload_media: true,
      processing_mode: 'semantic_and_vectors',
      strict: false,
      telemetry: true,
      to: 'viking://resources/tos-item',
      wait: false,
    })
  })

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
