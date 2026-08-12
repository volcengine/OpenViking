import type { FeishuAuthMode } from '../-components/feishu-resource-options'
import type {
  GitAuthMode,
  GitRefMode,
} from '../-components/git-resource-options'
import type { WebResourceOptionsValue } from '../-components/web-resource-options'
import {
  parseDelimitedValues,
  parseOptionalNumber,
} from './resource-option-values'
import type { RemoteResourceKind } from './resource-source'
import type { ResourceImportCommonBody } from './resource-import-types'

type RemoteResourceCapabilities = {
  exactDestination: boolean
  nativeOptions: boolean
  watch: boolean
}

type RemoteSourceRequestOptions = Pick<
  ResourceImportCommonBody,
  'add_type' | 'args' | 'exclude' | 'include'
>

type RemoteSourceStrategy = {
  build: (state: RemoteSourceOptionState) => RemoteSourceRequestOptions
  capabilities: RemoteResourceCapabilities
}

const DEFAULT_CAPABILITIES: RemoteResourceCapabilities = {
  exactDestination: false,
  nativeOptions: true,
  watch: true,
}

const TOS_CAPABILITIES: RemoteResourceCapabilities = {
  exactDestination: true,
  nativeOptions: false,
  watch: false,
}

export type RemoteSourceOptionState = {
  feishu: {
    accessToken: string
    authMode: FeishuAuthMode
    refreshToken: string
  }
  git: {
    authMode: GitAuthMode
    refMode: GitRefMode
    refValue: string
    supportsHttpAuth: boolean
    token: string
    username: string
  }
  watchEnabled: boolean
  web: WebResourceOptionsValue
}

const DEFAULT_STRATEGY: RemoteSourceStrategy = {
  build: () => ({}),
  capabilities: DEFAULT_CAPABILITIES,
}

const FEISHU_STRATEGY: RemoteSourceStrategy = {
  build: (state) =>
    state.feishu.authMode === 'user'
      ? {
          args: {
            feishu_access_token: state.feishu.accessToken.trim(),
            ...(state.watchEnabled
              ? { feishu_refresh_token: state.feishu.refreshToken.trim() }
              : {}),
          },
        }
      : {},
  capabilities: DEFAULT_CAPABILITIES,
}

const GIT_STRATEGY: RemoteSourceStrategy = {
  build: (state) => ({
    args: {
      ...(state.git.refValue.trim()
        ? { [state.git.refMode]: state.git.refValue.trim() }
        : {}),
      ...(state.git.authMode === 'token' && state.git.supportsHttpAuth
        ? {
            auth_config: {
              username: state.git.username.trim() || 'oauth2',
              token: state.git.token.trim(),
            },
          }
        : {}),
    },
  }),
  capabilities: DEFAULT_CAPABILITIES,
}

const WEB_STRATEGY: RemoteSourceStrategy = {
  build: (state) => {
    const includePaths = parseDelimitedValues(state.web.includePaths)
    const excludePaths = parseDelimitedValues(state.web.excludePaths)
    const recursive = state.web.mode === 'recursive'
    const site = state.web.mode === 'site'
    return {
      args: {
        ...(state.web.mode === 'single' ? { site: false, depth: 0 } : {}),
        ...(recursive
          ? {
              site: false,
              depth: parseOptionalNumber(state.web.depth),
              max_pages: parseOptionalNumber(state.web.maxPages),
              include_paths: includePaths.length ? includePaths : undefined,
              exclude_paths: excludePaths.length ? excludePaths : undefined,
              allow_external_links: state.web.allowExternalLinks,
              skip_download_links: state.web.skipDownloadLinks,
            }
          : {}),
        ...(site
          ? {
              site: true,
              max_pages: parseOptionalNumber(state.web.maxPages),
            }
          : {}),
      },
      ...(site && includePaths.length
        ? { include: includePaths.join(',') }
        : {}),
      ...(site && excludePaths.length
        ? { exclude: excludePaths.join(',') }
        : {}),
    }
  },
  capabilities: DEFAULT_CAPABILITIES,
}

const TOS_STRATEGY: RemoteSourceStrategy = {
  build: () => ({ add_type: 'tos' }),
  capabilities: TOS_CAPABILITIES,
}

const REMOTE_SOURCE_STRATEGIES: Record<
  RemoteResourceKind,
  RemoteSourceStrategy
> = {
  unknown: DEFAULT_STRATEGY,
  feishu: FEISHU_STRATEGY,
  git: GIT_STRATEGY,
  webFeed: WEB_STRATEGY,
  webPage: WEB_STRATEGY,
  remoteFile: DEFAULT_STRATEGY,
  tos: TOS_STRATEGY,
}

function getRemoteSourceStrategy(kind: RemoteResourceKind) {
  return REMOTE_SOURCE_STRATEGIES[kind]
}

export function getRemoteResourceCapabilities(
  kind: RemoteResourceKind,
): RemoteResourceCapabilities {
  return getRemoteSourceStrategy(kind).capabilities
}

export function buildRemoteSourceRequestOptions(
  kind: RemoteResourceKind,
  state: RemoteSourceOptionState,
): RemoteSourceRequestOptions {
  return getRemoteSourceStrategy(kind).build(state)
}
