import { describe, expect, it } from 'vitest'

import {
  buildRemoteSourceRequestOptions,
  getRemoteResourceCapabilities,
  isRemoteSourceConfigurationValid,
} from './resource-source-strategy'
import type { RemoteSourceOptionState } from './resource-source-strategy'

const DEFAULT_STATE: RemoteSourceOptionState = {
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
  it('declares TOS as an exact, connector-only, non-watchable source', () => {
    expect(getRemoteResourceCapabilities('tos')).toEqual({
      exactDestination: true,
      nativeOptions: false,
      watch: false,
    })
    expect(buildRemoteSourceRequestOptions('tos', DEFAULT_STATE)).toEqual({
      add_type: 'tos',
    })
  })

  it('builds and validates watched Feishu user credentials together', () => {
    const state: RemoteSourceOptionState = {
      ...DEFAULT_STATE,
      feishu: {
        accessToken: 'u-token',
        authMode: 'user',
        refreshToken: 'r-token',
      },
      watchEnabled: true,
    }

    expect(isRemoteSourceConfigurationValid('feishu', state)).toBe(true)
    expect(buildRemoteSourceRequestOptions('feishu', state)).toEqual({
      args: {
        feishu_access_token: 'u-token',
        feishu_refresh_token: 'r-token',
      },
    })
  })

  it('validates recursive web limits in the same strategy that builds them', () => {
    const state: RemoteSourceOptionState = {
      ...DEFAULT_STATE,
      web: {
        ...DEFAULT_STATE.web,
        depth: '-1',
        mode: 'recursive',
      },
    }

    expect(isRemoteSourceConfigurationValid('webPage', state)).toBe(false)
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

    expect(isRemoteSourceConfigurationValid('git', state)).toBe(false)
    expect(buildRemoteSourceRequestOptions('git', state)).toEqual({ args: {} })
  })
})
