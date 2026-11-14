import { useEffect, useMemo, useSyncExternalStore } from 'react';
import type { VikingBotAttachment } from '../../../components/viking-bot/model.js';
import type { VikingBotAttachmentUpload } from './types.js';

const NO_ATTACHMENTS: readonly VikingBotAttachment[] = [];

function createUploadOwner(
  config?: VikingBotAttachmentUpload,
  parentSignal?: AbortSignal,
) {
  let items = NO_ATTACHMENTS;
  let sequence = 0;
  const requests = new Map<string, AbortController>();
  const listeners = new Set<() => void>();
  const update = (next: typeof items) => {
    items = next;
    listeners.forEach((listener) => listener());
  };
  const clear = () => {
    requests.forEach((request) => request.abort());
    requests.clear();
    if (items.length) update(NO_ATTACHMENTS);
  };
  const remove = ({ key }: VikingBotAttachment) => {
    requests.get(key)?.abort();
    requests.delete(key);
    update(items.filter((item) => item.key !== key));
  };
  const removeSent = (attachments: readonly VikingBotAttachment[]) => {
    const keys = new Set(attachments.map(({ key }) => key));
    update(items.filter(({ key }) => !keys.has(key)));
  };
  return {
    clear,
    removeSent,
    remove,
    getSnapshot: () => items,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    add: (files: readonly File[]) => {
      if (!config || parentSignal?.aborted) return;
      for (const file of files.slice(0, Math.max(0, 10 - items.length))) {
        const request = new AbortController();
        const signal = parentSignal
          ? AbortSignal.any([request.signal, parentSignal])
          : request.signal;
        const item: VikingBotAttachment = {
          key: String(++sequence),
          name: file.name,
          mimeType: file.type,
          sizeBytes: file.size,
          status: 'uploading',
        };
        requests.set(item.key, request);
        update([...items, item]);
        const finish = (result: Partial<VikingBotAttachment>) => {
          if (!requests.delete(item.key) || signal.aborted) return;
          update(
            items.map((entry) =>
              entry === item ? { ...entry, ...result } : entry,
            ),
          );
        };
        void Promise.resolve()
          .then(() => {
            signal.throwIfAborted();
            return config.upload(file, signal);
          })
          .then((id) => {
            const uploadId = id?.trim();
            if (!uploadId) throw new Error();
            finish({ status: 'ready', uploadId });
          })
          .catch((error: unknown) =>
            finish({
              status: 'error',
              errorMessage:
                error instanceof Error ? error.message || undefined : undefined,
            }),
          );
      }
    },
  };
}

export function useAttachmentUpload(
  config: VikingBotAttachmentUpload | undefined,
  chatOwner: unknown,
  signal?: AbortSignal,
) {
  const owner = useMemo(() => {
    void chatOwner;
    return createUploadOwner(config, signal);
  }, [config, chatOwner, signal]);
  // Effect necessity: uploads follow the external chat controller and its
  // inherited/request signal, whose cancellation can happen outside UI events.
  // Cleanup removes the abort subscription and cancels this owner's requests.
  useEffect(() => {
    if (signal?.aborted) owner.clear();
    else signal?.addEventListener('abort', owner.clear, { once: true });
    return () => {
      signal?.removeEventListener('abort', owner.clear);
      owner.clear();
    };
  }, [owner, signal]);
  const attachments = useSyncExternalStore(
    owner.subscribe,
    owner.getSnapshot,
    () => NO_ATTACHMENTS,
  );
  return {
    clear: owner.clear,
    removeSent: owner.removeSent,
    props: config
      ? {
          accept: config.accept,
          attachments,
          onAttachmentRemove: owner.remove,
          onFilesAdded: owner.add,
        }
      : undefined,
  };
}
