import { describe, expect, it } from 'vitest'

import {
  buildRemoteSourceRequestOptions,
  getRemoteResourceCapabilities,
} from './resource-source-strategy'
import type { RemoteSourceOptionState } from './resource-source-strategy'

const DEFAULT_STATE: RemoteSourceOptionState = {
  dingtalk: {
    identity: '',
    maxBytesMiB: '512',
    maxDepth: '20',
    maxNodes: '1000',
  },
  feishu: {
    accessToken: '',
    authMode: 'app',
    refreshToken: '',
  },
  git: {
    authMode: 'public',
    refMode: 'branch',
    refValue: '',
    supportsHttpAuth: false,
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
}

describe('remote resource source strategy', () => {
  it('builds native DingTalk identity and byte limits', () => {
    expect(
      buildRemoteSourceRequestOptions('dingtalk', {
        ...DEFAULT_STATE,
        dingtalk: { ...DEFAULT_STATE.dingtalk, identity: 'team-docs' },
      }),
    ).toEqual({
      args: {
        dingtalk_identity: 'team-docs',
        dingtalk_max_bytes: 512 * 1024 * 1024,
        dingtalk_max_depth: 20,
        dingtalk_max_nodes: 1000,
      },
    })
  })

  it('declares TOS as an exact, connector-only, non-watchable source', () => {
    expect(getRemoteResourceCapabilities('tos')).toEqual({
      exactDestination: true,
      initialPaused: false,
      nativeOptions: false,
      watch: false,
    })
    expect(buildRemoteSourceRequestOptions('tos', DEFAULT_STATE)).toEqual({
      add_type: 'tos',
    })
  })

  it('exposes initial pause only for DingTalk', () => {
    expect(getRemoteResourceCapabilities('dingtalk').initialPaused).toBe(true)
    expect(getRemoteResourceCapabilities('feishu').initialPaused).toBe(false)
    expect(getRemoteResourceCapabilities('git').initialPaused).toBe(false)
    expect(getRemoteResourceCapabilities('webPage').initialPaused).toBe(false)
    expect(getRemoteResourceCapabilities('remoteFile').initialPaused).toBe(
      false,
    )
  })

  it('builds watched Feishu user credentials', () => {
    const state: RemoteSourceOptionState = {
      ...DEFAULT_STATE,
      feishu: {
        accessToken: 'u-token',
        authMode: 'user',
        refreshToken: 'r-token',
      },
      watchEnabled: true,
    }

    expect(buildRemoteSourceRequestOptions('feishu', state)).toEqual({
      args: {
        feishu_access_token: 'u-token',
        feishu_refresh_token: 'r-token',
      },
    })
  })

  it('forwards recursive web limits for server validation', () => {
    const state: RemoteSourceOptionState = {
      ...DEFAULT_STATE,
      web: {
        ...DEFAULT_STATE.web,
        depth: '-1',
        mode: 'recursive',
      },
    }

    expect(buildRemoteSourceRequestOptions('webPage', state)).toEqual({
      args: {
        allow_external_links: false,
        depth: -1,
        exclude_paths: undefined,
        include_paths: undefined,
        max_pages: 50,
        site: false,
        skip_download_links: true,
      },
    })
  })

  it('builds site filters as top-level comma-delimited patterns', () => {
    const state: RemoteSourceOptionState = {
      ...DEFAULT_STATE,
      web: {
        ...DEFAULT_STATE.web,
        excludePaths: '/archive\n/private',
        includePaths: '/docs, /zh/',
        mode: 'site',
      },
    }

    expect(buildRemoteSourceRequestOptions('webPage', state)).toEqual({
      args: {
        max_pages: 50,
        site: true,
      },
      exclude: '/archive,/private',
      include: '/docs,/zh/',
    })
  })

  it('does not build token authentication for a non-HTTPS Git source', () => {
    const state: RemoteSourceOptionState = {
      ...DEFAULT_STATE,
      git: {
        ...DEFAULT_STATE.git,
        authMode: 'token',
        token: 'secret-token',
      },
    }

    expect(buildRemoteSourceRequestOptions('git', state)).toEqual({ args: {} })
  })
})
