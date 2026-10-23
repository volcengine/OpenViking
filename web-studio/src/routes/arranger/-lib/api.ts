import { sha256 } from '@noble/hashes/sha2.js'
import { bytesToHex } from '@noble/hashes/utils.js'
import { parse as parseYaml } from 'yaml'

import i18n from '#/i18n/core'
import {
  getFsStat,
  getOvClient,
  getOvResult,
  isOvClientError,
  normalizeOvClientError,
  postFsMkdir,
  postFsMv,
} from '#/lib/ov-client'
import {
  fileNameFromUri,
  normalizeFsEntries,
} from '#/routes/resources/-lib/normalize'
import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'

import type {
  JsonSchema,
  LiveContextMount,
  LiveMountIndex,
  LoadedDirectory,
  McpCatalog,
  McpCatalogTool,
  MountInput,
  MutationKind,
  TextDocument,
} from './model'
import { assertMutationBoundary, isWithinRoot, normalizeVikingUri } from './uri'

type Envelope<T> = {
  has_more?: boolean
  result: T
  status: string
}

const SAVE_RETRY_DELAYS_MS = [250, 500, 1_000, 2_000, 4_000]

function isRetryableWriteConflict(error: unknown): boolean {
  if (!isOvClientError(error) || error.code !== 'CONFLICT') return false
  return (
    error.details !== null &&
    typeof error.details === 'object' &&
    'retryable' in error.details &&
    error.details.retryable === true
  )
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

function apiBaseUrl(): string {
  return getOvClient().getOptions().baseUrl
}

type ApiResource =
  | 'filesystemStat'
  | 'mcpMountList'
  | 'mcpServerList'
  | 'mcpToolCatalog'

function invalidApiResponse(resource: ApiResource, value: unknown): Error {
  if (typeof value === 'string' && /<!doctype\s+html|<html[\s>]/i.test(value)) {
    return new Error(i18n.t('arranger:errors.studioHtmlResponse'))
  }
  return new Error(
    i18n.t('arranger:errors.invalidApiResponse', {
      resource: i18n.t(`arranger:apiResources.${resource}`),
    }),
  )
}

export function parseMcpServers(value: unknown): Array<{ name: string }> {
  const servers = Array.isArray(value)
    ? value
    : value &&
        typeof value === 'object' &&
        'servers' in value &&
        Array.isArray(value.servers)
      ? value.servers
      : null
  if (!servers) throw invalidApiResponse('mcpServerList', value)
  if (
    servers.some(
      (server) =>
        !server ||
        typeof server !== 'object' ||
        !('name' in server) ||
        typeof server.name !== 'string' ||
        !server.name,
    )
  ) {
    throw invalidApiResponse('mcpServerList', value)
  }
  return servers as Array<{ name: string }>
}

export function parseMcpMounts(value: unknown): LiveContextMount[] {
  const mounts = Array.isArray(value)
    ? value
    : value &&
        typeof value === 'object' &&
        'mounts' in value &&
        Array.isArray(value.mounts)
      ? value.mounts
      : null
  if (!mounts) throw invalidApiResponse('mcpMountList', value)
  return mounts as LiveContextMount[]
}

export function parseMcpTools(
  server: string,
  value: unknown,
): McpCatalogTool[] {
  const tools =
    value &&
    typeof value === 'object' &&
    'tools' in value &&
    Array.isArray(value.tools)
      ? value.tools
      : null
  if (!tools) throw invalidApiResponse('mcpToolCatalog', value)
  return tools.map((document) => {
    if (
      !document ||
      typeof document !== 'object' ||
      !('tool' in document) ||
      !document.tool ||
      typeof document.tool !== 'object' ||
      !('name' in document.tool) ||
      typeof document.tool.name !== 'string'
    ) {
      throw invalidApiResponse('mcpToolCatalog', value)
    }
    const tool = document.tool as Record<string, unknown>
    const name = tool.name as string
    return {
      input_revision:
        'input_revision' in document &&
        typeof document.input_revision === 'string'
          ? document.input_revision
          : '',
      revision:
        'revision' in document && typeof document.revision === 'string'
          ? document.revision
          : '',
      server,
      tool: {
        annotations:
          tool.annotations && typeof tool.annotations === 'object'
            ? (tool.annotations as Record<string, unknown>)
            : undefined,
        description:
          typeof tool.description === 'string' ? tool.description : undefined,
        inputSchema:
          tool.inputSchema && typeof tool.inputSchema === 'object'
            ? (tool.inputSchema as JsonSchema)
            : { type: 'object' },
        name,
        title: typeof tool.title === 'string' ? tool.title : undefined,
      },
      uri: `viking://agent/tools/${server}/${name}.json`,
    }
  })
}

export async function loadMcpCatalog(): Promise<McpCatalog> {
  const serverResult = await getOvResult<unknown>(
    getOvClient().instance.get('/api/v1/mcps', {
      baseURL: apiBaseUrl(),
    }),
  )
  const servers = parseMcpServers(serverResult)
  const settled = await Promise.allSettled(
    servers.map(async ({ name }) => {
      const result = await getOvResult<unknown>(
        getOvClient().instance.get(`/api/v1/mcps/${encodeURIComponent(name)}`, {
          baseURL: apiBaseUrl(),
        }),
      )
      return { name, tools: parseMcpTools(name, result) }
    }),
  )
  const loaded = settled.flatMap((item) =>
    item.status === 'fulfilled' ? [item.value] : [],
  )
  const failed = settled.find(
    (item): item is PromiseRejectedResult => item.status === 'rejected',
  )
  return {
    complete: !failed,
    error: failed ? normalizeOvClientError(failed.reason).message : undefined,
    servers: loaded,
    tools: loaded.flatMap((server) => server.tools),
  }
}

export async function statResource(uri: string): Promise<VikingFsEntry> {
  const normalized = normalizeVikingUri(uri)
  const result = await getOvResult<Record<string, unknown>>(
    getFsStat({ query: { uri: normalized } }),
  )
  if (
    typeof result !== 'object' ||
    (!('isDir' in result) && !('is_dir' in result))
  ) {
    throw invalidApiResponse('filesystemStat', result)
  }
  return {
    abstract: String(result.abstract ?? ''),
    isDir: Boolean(result.isDir ?? result.is_dir),
    modTime: String(result.modTime ?? result.mod_time ?? ''),
    modTimestamp: null,
    name: fileNameFromUri(normalized),
    overview: String(result.overview ?? ''),
    size: String(result.size ?? ''),
    sizeBytes:
      typeof result.size === 'number'
        ? result.size
        : typeof result.size_bytes === 'number'
          ? result.size_bytes
          : null,
    uri: normalized,
  }
}

export async function listDirectory(
  uri: string,
  offset = 0,
  limit = 200,
  recursive = false,
): Promise<LoadedDirectory> {
  const normalized = normalizeVikingUri(uri)
  const response = await getOvClient().instance.get<Envelope<unknown>>(
    '/api/v1/fs/ls',
    {
      baseURL: apiBaseUrl(),
      params: {
        uri: normalized,
        output: 'agent',
        show_all_hidden: true,
        include_abstract: true,
        recursive,
        offset,
        limit,
        sort_by: 'name',
        sort_order: 'asc',
      },
    },
  )
  const entries = normalizeFsEntries(response.data.result, normalized)
  return {
    entries,
    hasMore: Boolean(response.data.has_more),
    nextOffset: offset + entries.length,
  }
}

export async function readTextDocument(uri: string): Promise<TextDocument> {
  const response = await getOvClient().instance.get<ArrayBuffer>(
    '/api/v1/content/download',
    {
      baseURL: apiBaseUrl(),
      params: { uri },
      responseType: 'arraybuffer',
    },
  )
  const bytes = new Uint8Array(response.data)
  return {
    bytes,
    content: new TextDecoder('utf-8', { fatal: true }).decode(bytes),
    sha256: bytesToHex(sha256(bytes)),
  }
}

export async function readLiveContext(uri: string): Promise<unknown> {
  return getOvResult<unknown>(
    getOvClient().instance.get('/api/v1/content/read', {
      baseURL: apiBaseUrl(),
      params: {
        uri: normalizeVikingUri(uri),
        offset: 0,
        limit: -1,
        raw: false,
      },
    }),
  )
}

export function hashText(content: string): string {
  return bytesToHex(sha256(new TextEncoder().encode(content)))
}

export async function createDirectory(
  uri: string,
  description?: string,
): Promise<void> {
  await getOvResult(
    postFsMkdir({ body: { uri: normalizeVikingUri(uri), description } }),
  )
}

export async function createText(uri: string, content = ''): Promise<void> {
  await getOvClient().instance.post(
    '/api/v1/content/batch-write',
    {
      root_uri: normalizeVikingUri(uri).split('/').slice(0, -1).join('/'),
      wait: false,
      operations: [
        {
          uri: normalizeVikingUri(uri),
          content,
          mode: 'create',
          if_absent: true,
        },
      ],
    },
    { baseURL: apiBaseUrl() },
  )
}

export async function saveText(
  uri: string,
  content: string,
  expectedSha256: string,
): Promise<void> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      await getOvClient().instance.post(
        '/api/v1/content/batch-write',
        {
          root_uri: normalizeVikingUri(uri).split('/').slice(0, -1).join('/'),
          wait: false,
          operations: [
            {
              uri: normalizeVikingUri(uri),
              content,
              mode: 'replace',
              expected_sha256: expectedSha256,
            },
          ],
        },
        { baseURL: apiBaseUrl() },
      )
      return
    } catch (error) {
      if (
        attempt >= SAVE_RETRY_DELAYS_MS.length ||
        !isRetryableWriteConflict(error)
      ) {
        throw error
      }
      await delay(SAVE_RETRY_DELAYS_MS[attempt])
    }
  }
}

export async function moveResource(
  source: string,
  target: string,
): Promise<void> {
  await getOvResult(
    postFsMv({
      body: {
        from_uri: normalizeVikingUri(source),
        to_uri: normalizeVikingUri(target),
      },
    }),
  )
}

export async function copyResource(
  source: string,
  target: string,
  recursive: boolean,
): Promise<void> {
  await getOvClient().instance.post(
    '/api/v1/fs/cp',
    {
      from_uri: normalizeVikingUri(source),
      to_uri: normalizeVikingUri(target),
      recursive,
    },
    { baseURL: apiBaseUrl() },
  )
}

export async function removeResource(
  uri: string,
  recursive: boolean,
): Promise<void> {
  await getOvClient().instance.delete('/api/v1/fs', {
    baseURL: apiBaseUrl(),
    params: { uri: normalizeVikingUri(uri), recursive, wait: false },
  })
}

export async function loadLiveMounts(rootUri: string): Promise<LiveMountIndex> {
  try {
    const serverResult = await getOvResult<unknown>(
      getOvClient().instance.get('/api/v1/mcps', {
        baseURL: apiBaseUrl(),
      }),
    )
    const servers = parseMcpServers(serverResult)
    const settled = await Promise.allSettled(
      servers.map(async ({ name }) => {
        const mountResult = await getOvResult<unknown>(
          getOvClient().instance.get('/api/v1/mcps/mounts', {
            baseURL: apiBaseUrl(),
            params: { mcp: name },
          }),
        )
        return parseMcpMounts(mountResult)
      }),
    )
    const mounts = new Map<string, LiveContextMount>()
    for (const item of settled) {
      if (item.status !== 'fulfilled') continue
      for (const mount of item.value) {
        if (isWithinRoot(mount.node_uri, rootUri)) {
          mounts.set(normalizeVikingUri(mount.node_uri), mount)
        }
      }
    }
    const failed = settled.find(
      (item): item is PromiseRejectedResult => item.status === 'rejected',
    )
    return {
      complete: !failed,
      error: failed ? normalizeOvClientError(failed.reason).message : undefined,
      mounts,
    }
  } catch (error) {
    return {
      complete: false,
      error: normalizeOvClientError(error).message,
      mounts: new Map(),
    }
  }
}

export async function mountLiveContext(input: MountInput): Promise<void> {
  await getOvClient().instance.post(
    '/api/v1/mcps/mount',
    {
      ...input,
      node_uri: normalizeVikingUri(input.node_uri),
      wait: false,
    },
    { baseURL: apiBaseUrl() },
  )
}

export async function batchMountLiveContexts(
  mounts: MountInput[],
): Promise<void> {
  await getOvClient().instance.post(
    '/api/v1/mcps/batch-mount',
    {
      mounts: mounts.map((mount) => ({
        ...mount,
        node_uri: normalizeVikingUri(mount.node_uri),
      })),
      wait: false,
    },
    { baseURL: apiBaseUrl() },
  )
}

export async function unmountLiveContext(nodeUri: string): Promise<void> {
  await getOvClient().instance.delete('/api/v1/mcps/mount', {
    baseURL: apiBaseUrl(),
    params: { node_uri: normalizeVikingUri(nodeUri), wait: false },
  })
}

export async function batchUnmountLiveContexts(
  nodeUris: string[],
): Promise<void> {
  await getOvClient().instance.post(
    '/api/v1/mcps/batch-unmount',
    {
      node_uris: nodeUris.map(normalizeVikingUri),
      wait: false,
    },
    { baseURL: apiBaseUrl() },
  )
}

export async function moveLiveContext(
  mount: LiveContextMount,
  target: string,
): Promise<void> {
  const sourceUri = normalizeVikingUri(mount.node_uri)
  const targetUri = normalizeVikingUri(target)
  if (sourceUri === targetUri) return

  const input: MountInput = {
    arguments: mount.arguments,
    description: mount.description,
    node_uri: targetUri,
    tags: mount.tags,
    timeout_seconds: mount.timeout_seconds || 60,
    title: mount.title || fileNameFromUri(sourceUri),
    tool_uri: mount.tool_uri,
  }
  await mountLiveContext(input)
  try {
    await unmountLiveContext(sourceUri)
  } catch (error) {
    try {
      await unmountLiveContext(input.node_uri)
    } catch {
      // The caller refreshes mounts and presents the actual state.
    }
    throw error
  }
}

export function isLiveContextDocument(content: string): boolean {
  return parseLiveContextMetadata(content) !== null
}

export function parseLiveContextMetadata(
  content: string,
): Partial<MountInput> | null {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(content)
  if (!match) return null
  try {
    const metadata = parseYaml(match[1]) as Record<string, unknown> | null
    if (metadata?.type !== 'OpenViking Live Context') return null
    const extension =
      metadata['x-openviking'] && typeof metadata['x-openviking'] === 'object'
        ? (metadata['x-openviking'] as Record<string, unknown>)
        : {}
    return {
      arguments:
        extension.args && typeof extension.args === 'object'
          ? (extension.args as Record<string, unknown>)
          : {},
      description:
        typeof metadata.description === 'string'
          ? metadata.description
          : undefined,
      timeout_seconds:
        typeof extension.timeout_seconds === 'number'
          ? extension.timeout_seconds
          : 60,
      title: typeof metadata.title === 'string' ? metadata.title : undefined,
      tool_uri:
        typeof extension.definition === 'string'
          ? extension.definition
          : undefined,
    }
  } catch {
    return {}
  }
}

async function assertOrdinaryFile(uri: string): Promise<void> {
  if (!uri.toLowerCase().endsWith('.md')) return
  const document = await readTextDocument(uri)
  if (isLiveContextDocument(document.content)) {
    throw new Error(
      i18n.t('arranger:errors.liveContextMutationRequired', { uri }),
    )
  }
}

export async function preflightOrdinaryMutation(options: {
  kind: MutationKind
  rootUri: string
  source: VikingFsEntry
  target?: string
  liveIndex: LiveMountIndex
}): Promise<void> {
  const { liveIndex, rootUri, source, target } = options
  assertMutationBoundary(source.uri, target, rootUri)
  if (!liveIndex.complete) {
    throw new Error(i18n.t('arranger:errors.mountInventoryIncomplete'))
  }
  if (liveIndex.mounts.has(normalizeVikingUri(source.uri))) {
    throw new Error(i18n.t('arranger:errors.liveContextMutationOnly'))
  }
  if (target && liveIndex.mounts.has(normalizeVikingUri(target))) {
    throw new Error(i18n.t('arranger:errors.destinationLiveContext'))
  }

  if (!source.isDir) {
    await assertOrdinaryFile(source.uri)
    return
  }

  const prefix = `${normalizeVikingUri(source.uri)}/`
  if ([...liveIndex.mounts.keys()].some((uri) => uri.startsWith(prefix))) {
    throw new Error(i18n.t('arranger:errors.directoryContainsLiveContext'))
  }

  let offset = 0
  for (;;) {
    let page: LoadedDirectory
    try {
      page = await listDirectory(source.uri, offset, 500, true)
      await Promise.all(
        page.entries
          .filter((entry) => !entry.isDir)
          .map((entry) => assertOrdinaryFile(entry.uri)),
      )
    } catch (error) {
      throw new Error(
        i18n.t('arranger:errors.preflightFailedWithDetail', {
          detail: normalizeOvClientError(error).message,
        }),
      )
    }
    if (!page.hasMore) return
    if (page.nextOffset <= offset) {
      throw new Error(i18n.t('arranger:errors.preflightFailed'))
    }
    offset = page.nextOffset
  }
}

export async function mutateOrdinaryResource(options: {
  kind: MutationKind
  rootUri: string
  source: VikingFsEntry
  target?: string
  liveIndex: LiveMountIndex
}): Promise<void> {
  await preflightOrdinaryMutation(options)
  if (options.kind === 'delete') {
    await removeResource(options.source.uri, options.source.isDir)
  } else if (options.kind === 'copy' && options.target) {
    await copyResource(options.source.uri, options.target, options.source.isDir)
  } else if (options.kind === 'move' && options.target) {
    await moveResource(options.source.uri, options.target)
  }
}
