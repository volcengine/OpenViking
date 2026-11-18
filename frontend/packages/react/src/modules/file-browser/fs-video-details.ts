import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type {
  FilePreviewFileTab,
  FilePreviewTab,
} from '../../components/file-preview/index.js';
import { PAGE_SIZE } from '../../shared/directory-tree/fs-page.js';
import { nameFromUri } from '../../shared/directory-tree/rows.js';
import type { FsEntry } from '../../shared/directory-tree/types.js';
import { mimeTypeOf } from './fs-file-kind.js';
import { readPreviewImage } from './fs-preview-images.js';

type VideoDetails = NonNullable<FilePreviewFileTab['videoDetails']>;
type Segment = VideoDetails['segments'][number];

function parseTranscript(content: string): Segment[] {
  const info: unknown = JSON.parse(content);
  if (
    !info ||
    typeof info !== 'object' ||
    !('audio_segments' in info) ||
    !Array.isArray(info.audio_segments)
  ) {
    throw new Error('Invalid video segment info');
  }
  return info.audio_segments
    .flatMap((segment: unknown) => {
      if (!segment || typeof segment !== 'object') return [];
      const { start_time, end_time, content, speaker } = segment as Record<
        string,
        unknown
      >;
      if (
        typeof start_time !== 'number' ||
        !Number.isFinite(start_time) ||
        start_time < 0 ||
        typeof end_time !== 'number' ||
        !Number.isFinite(end_time) ||
        end_time < start_time ||
        typeof content !== 'string'
      )
        return [];
      const speakerId =
        speaker && typeof speaker === 'object' && 'speaker_id' in speaker
          ? speaker.speaker_id
          : undefined;
      return [
        {
          startMs: start_time,
          endMs: end_time,
          text: content,
          speaker: typeof speakerId === 'string' ? speakerId : undefined,
        },
      ];
    })
    .sort((a, b) => a.startMs - b.startMs);
}

/** A processed segment has one video and a segment_info.json in the same directory. */
async function findVideoArtifacts(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) {
  const slash = uri.lastIndexOf('/');
  if (slash < 'viking://'.length) return;
  const parent = uri.slice(0, slash);
  const files = new Map<string, FsEntry>();
  let offset = 0;
  while (true) {
    options.signal?.throwIfAborted();
    const page = await client.requestEnvelope<FsEntry[]>({
      method: 'GET',
      path: OV_PATHS.fsLs,
      query: {
        uri: parent,
        output: 'agent',
        include_abstract: false,
        node_limit: PAGE_SIZE,
        offset,
      },
      options,
    });
    const entries = page.result ?? [];
    for (const entry of entries) {
      if (
        !entry.isDir &&
        entry.access !== 'denied' &&
        entry.access !== 'container'
      ) {
        files.set(entry.uri, entry);
      }
    }
    if (!page.hasMore || !entries.length) break;
    offset += entries.length;
  }
  const siblings = [...files.keys()];
  const metadata = siblings.find(
    (file) => nameFromUri(file) === 'segment_info.json',
  );
  const [video, anotherVideo] = siblings.filter((file) =>
    mimeTypeOf(file).startsWith('video/'),
  );
  if (
    !metadata ||
    !video ||
    anotherVideo ||
    nameFromUri(video) !== nameFromUri(uri)
  )
    return;
  return {
    metadata,
    frames: siblings
      .filter((file) => mimeTypeOf(file).startsWith('image/'))
      .sort((a, b) =>
        nameFromUri(a).localeCompare(nameFromUri(b), undefined, {
          numeric: true,
        }),
      ),
  };
}

/** Publish details independently of playback. The preview's signal owns every request. */
export async function loadVideoDetails(
  client: OvClient,
  tab: FilePreviewTab,
  options: OvRequestOptions,
  publish: (tab: FilePreviewFileTab) => void,
) {
  if (tab.kind !== 'file') return;
  const uri = tab.source.path;
  if (!uri || !tab.source.file?.type.startsWith('video/')) return;
  const artifacts = await findVideoArtifacts(client, uri, options);
  if (!artifacts || options.signal?.aborted) return;
  let segments: Segment[] = [];
  const frames = new Map<string, VideoDetails['frames'][number]>(
    artifacts.frames.map((id) => [id, { id, name: nameFromUri(id) }]),
  );
  let loading = false;
  const onLoadFrames = () => void loadFrames();
  const update = () => {
    if (!options.signal?.aborted) {
      publish({
        ...tab,
        videoDetails: {
          segments,
          frames: [...frames.values()],
          framesLoading: loading,
          onLoadFrames,
          onFrameError: (id, src) => {
            const frame = frames.get(id);
            if (!frame || frame.src !== src || options.signal?.aborted) return;
            frames.set(id, { ...frame, src: undefined, failed: true });
            update();
          },
        },
      });
    }
  };
  async function loadFrames() {
    const pending = [...frames.values()].filter(
      (frame) => frame.src === undefined,
    );
    if (!pending.length || loading || options.signal?.aborted) return;
    loading = true;
    for (const frame of pending)
      frames.set(frame.id, { ...frame, failed: undefined });
    update();
    const queue = pending.values();
    const load = async () => {
      while (!options.signal?.aborted) {
        const { done, value: frame } = queue.next();
        if (done) return;
        try {
          const src = await readPreviewImage(
            client,
            frame.id,
            mimeTypeOf(frame.id),
            options,
          );
          frames.set(frame.id, { ...frame, src, failed: undefined });
        } catch {
          frames.set(frame.id, { ...frame, failed: true });
        }
        update();
      }
    };
    await Promise.all(
      Array.from({ length: Math.min(4, pending.length) }, load),
    );
    loading = false;
    update();
  }
  // Readable frames remain available while the transcript loads or fails.
  update();
  const content = await client.request<string>({
    method: 'GET',
    path: OV_PATHS.contentRead,
    query: { uri: artifacts.metadata, raw: true },
    options,
  });
  segments = parseTranscript(content);
  update();
}
