import i18n from '#/i18n/core'
import { normalizeOvClientError } from '#/shared/api/ov-client'
import type { VikingFsEntry } from '#/shared/types/viking-fs'

import {
  createDirectory,
  listDirectory,
  loadLiveMounts,
  mountLiveContext,
  moveResource,
  parseLiveContextMetadata,
  readTextDocument,
  removeResource,
  statResource,
  unmountLiveContext,
} from './api'
import type { MountInput } from './model'
import {
  assertMutationBoundary,
  isWithinRoot,
  nameFromVikingUri,
  normalizeVikingUri,
  parentVikingUri,
} from './uri'

export class RelocationError extends Error {
  constructor(
    source: string,
    target: string,
    completed: number,
    detail: unknown,
  ) {
    super(
      i18n.t('arranger:errors.relocationFailed', {
        source,
        target,
        completed,
        detail: normalizeOvClientError(detail).message,
      }),
    )
  }
}

async function assertAbsent(uri: string): Promise<void> {
  try {
    await statResource(uri)
  } catch (error) {
    if (normalizeOvClientError(error).code === 'NOT_FOUND') return
    throw error
  }
  throw new Error(i18n.t('arranger:errors.destinationExists', { uri }))
}

async function children(uri: string): Promise<VikingFsEntry[]> {
  const entries: VikingFsEntry[] = []
  let offset = 0
  for (;;) {
    const page = await listDirectory(uri, offset, 500)
    for (const entry of page.entries) {
      if (parentVikingUri(entry.uri) !== normalizeVikingUri(uri)) {
        throw new Error(i18n.t('arranger:errors.preflightFailed'))
      }
      entries.push({ ...entry, uri: normalizeVikingUri(entry.uri) })
    }
    if (!page.hasMore) return entries
    if (page.nextOffset <= offset) {
      throw new Error(i18n.t('arranger:errors.preflightFailed'))
    }
    offset = page.nextOffset
  }
}

/** Preflight the entire subtree before making any changes; never retry writes. */
export async function relocateResource(options: {
  rootUri: string
  source: VikingFsEntry
  target: string
}): Promise<string> {
  const root = normalizeVikingUri(options.rootUri)
  const source = normalizeVikingUri(options.source.uri)
  const target = normalizeVikingUri(options.target)
  if (!isWithinRoot(source, root)) {
    throw new Error(i18n.t('arranger:validation.destinationOutsideRoot'))
  }
  // Same-path requests are harmless, including normalized trailing slashes.
  if (source === target && source !== root) return target
  assertMutationBoundary(source, target, root)
  const index = await loadLiveMounts(root)
  if (!index.complete) {
    throw new Error(i18n.t('arranger:errors.mountInventoryIncomplete'))
  }
  await assertAbsent(target)
  if (!(await statResource(parentVikingUri(target))).isDir) {
    throw new Error(i18n.t('arranger:errors.destinationParentNotDirectory'))
  }

  const directories: VikingFsEntry[] = []
  const files: Array<{ entry: VikingFsEntry; mount?: MountInput }> = []
  const destination = (uri: string) => `${target}${uri.slice(source.length)}`
  const visit = async (entry: VikingFsEntry): Promise<void> => {
    const uri = normalizeVikingUri(entry.uri)
    const nodeUri = destination(uri)
    if (index.mounts.has(nodeUri)) {
      throw new Error(
        i18n.t('arranger:errors.destinationExists', { uri: nodeUri }),
      )
    }
    if (entry.isDir) {
      directories.push({ ...entry, uri })
      for (const child of await children(uri)) await visit(child)
      return
    }
    const indexed = index.mounts.get(uri)
    const metadata =
      indexed || uri.toLowerCase().endsWith('.md')
        ? parseLiveContextMetadata((await readTextDocument(uri)).content)
        : null
    if (indexed || metadata) {
      const config = { ...indexed, ...metadata }
      if (!config.tool_uri) {
        throw new Error(
          i18n.t('arranger:errors.liveContextMutationRequired', { uri }),
        )
      }
      files.push({
        entry: { ...entry, uri },
        mount: {
          arguments: config.arguments || {},
          description: config.description,
          node_uri: nodeUri,
          tags: indexed?.tags,
          timeout_seconds: config.timeout_seconds ?? 60,
          title: config.title || nameFromVikingUri(uri),
          tool_uri: config.tool_uri,
        },
      })
    } else {
      files.push({ entry: { ...entry, uri } })
    }
  }
  await visit(await statResource(source))
  const liveFiles = new Set(
    files.filter((file) => file.mount).map((file) => file.entry.uri),
  )
  if (
    [...index.mounts.keys()].some(
      (uri) => isWithinRoot(uri, source) && !liveFiles.has(uri),
    )
  ) {
    throw new Error(i18n.t('arranger:errors.mountInventoryIncomplete'))
  }

  let completed = 0
  let current = source
  try {
    for (const directory of directories) {
      current = directory.uri
      await createDirectory(
        destination(current),
        directory.overview || directory.abstract || undefined,
      )
    }
    for (const file of files) {
      current = file.entry.uri
      if (file.mount) {
        await assertAbsent(file.mount.node_uri)
        await mountLiveContext(file.mount, true)
        await unmountLiveContext(current, true)
      } else {
        await moveResource(current, destination(current))
      }
      completed += 1
    }
    for (const directory of [...directories].reverse()) {
      current = directory.uri
      // Existing APIs cannot make the empty check and removal atomic.
      if ((await children(current)).length) {
        throw new Error(
          i18n.t('arranger:errors.directoryChanged', { uri: current }),
        )
      }
      await removeResource(current, true, true)
    }
  } catch (error) {
    throw new RelocationError(current, destination(current), completed, error)
  }
  return target
}
