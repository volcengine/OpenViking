import type { FeishuAuthMode } from '../-components/feishu-resource-options'
import type {
  GitAuthMode,
  GitRefMode,
} from '../-components/git-resource-options'
import type { WebResourceOptionsValue } from '../-components/web-resource-options'
import {
  isOptionalIntegerValid,
  parseDelimitedValues,
  parseOptionalInteger,
} from './resource-option-values'
import type { RemoteResourceKind } from './resource-source'
import type { AddResourceCommonBody } from '@ov-server/api/v1/resources'

type RemoteResourceCapabilities = {
  exactDestination: boolean
  nativeOptions: boolean
  watch: boolean
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
    token: string
    username: string
  }
  watchEnabled: boolean
  web: WebResourceOptionsValue
}

export function getRemoteResourceCapabilities(
  kind: RemoteResourceKind,
): RemoteResourceCapabilities {
  return kind === 'tos' ? TOS_CAPABILITIES : DEFAULT_CAPABILITIES
}

export function buildRemoteSourceRequestOptions(
  kind: RemoteResourceKind,
  state: RemoteSourceOptionState,
): Pick<AddResourceCommonBody, 'add_type' | 'args'> {
  switch (kind) {
    case 'feishu':
      return state.feishu.authMode === 'user'
        ? {
            args: {
              feishu_access_token: state.feishu.accessToken.trim(),
              ...(state.watchEnabled
                ? { feishu_refresh_token: state.feishu.refreshToken.trim() }
                : {}),
            },
          }
        : {}
    case 'git':
      return {
        args: {
          ...(state.git.refValue.trim()
            ? { [state.git.refMode]: state.git.refValue.trim() }
            : {}),
          ...(state.git.authMode === 'token'
            ? {
                auth_config: {
                  username: state.git.username.trim() || 'oauth2',
                  token: state.git.token.trim(),
                },
              }
            : {}),
        },
      }
    case 'webFeed':
    case 'webPage': {
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
                depth: parseOptionalInteger(state.web.depth, 0),
                max_pages: parseOptionalInteger(state.web.maxPages, 1),
                include_paths: includePaths.length ? includePaths : undefined,
                exclude_paths: excludePaths.length ? excludePaths : undefined,
                allow_external_links: state.web.allowExternalLinks,
                skip_download_links: state.web.skipDownloadLinks,
              }
            : {}),
          ...(site
            ? {
                site: true,
                max_pages: parseOptionalInteger(state.web.maxPages, 1),
              }
            : {}),
        },
      }
    }
    case 'tos':
      return { add_type: 'tos' }
    default:
      return {}
  }
}

export function isRemoteSourceConfigurationValid(
  kind: RemoteResourceKind,
  state: RemoteSourceOptionState,
): boolean {
  switch (kind) {
    case 'feishu':
      return (
        state.feishu.authMode === 'app' ||
        (!!state.feishu.accessToken.trim() &&
          (!state.watchEnabled || !!state.feishu.refreshToken.trim()))
      )
    case 'git':
      return state.git.authMode === 'public' || !!state.git.token.trim()
    case 'webFeed':
    case 'webPage':
      return (
        (state.web.mode !== 'recursive' ||
          isOptionalIntegerValid(state.web.depth, 0)) &&
        (!['recursive', 'site'].includes(state.web.mode) ||
          isOptionalIntegerValid(state.web.maxPages, 1))
      )
    default:
      return true
  }
}
