import { toError } from '../fs-model.js';
import { useEffect, useRef, useState } from 'react';
import type {
  FileBrowserAclContext,
  FileBrowserAclEntry,
  FileBrowserAclReport,
} from '../acl-types.js';
import { readAcl, writeAcl } from './acl-client.js';
import { normalizeEntries, planChange } from './acl-model.js';

type Confirmation =
  { kind: 'restrict'; entries: FileBrowserAclEntry[] } | { kind: 'restore' };
export function useAclReport({
  uri,
  context,
  open,
  readOnly,
  onUpdated,
}: {
  uri: string;
  context: FileBrowserAclContext;
  open: boolean;
  readOnly: boolean;
  onUpdated: (report: FileBrowserAclReport) => void;
}) {
  const [report, setReport] = useState<FileBrowserAclReport>();
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const [error, setError] = useState<Error>();
  const [phase, setPhase] = useState<'loading' | 'ready' | 'saving'>('loading');
  const [saved, setSaved] = useState(false);
  const [generation, setGeneration] = useState(0);
  const pending = useRef(false);
  const lifetime = useRef<AbortController | null>(null);
  const latest = useRef({ context, onUpdated, readOnly });
  useEffect(() => {
    latest.current = { context, onUpdated, readOnly };
  });

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    lifetime.current = controller;
    pending.current = false;
    setPhase('loading');
    setReport(undefined);
    setError(undefined);
    setConfirmation(undefined);
    setSaved(false);
    const { client, requestOptions } = latest.current.context;
    const signal = requestOptions.signal
      ? AbortSignal.any([controller.signal, requestOptions.signal])
      : controller.signal;
    void readAcl(client, uri, { ...requestOptions, signal }).then(
      (value) => {
        if (!signal.aborted) {
          setReport(value);
          setPhase('ready');
        }
      },
      (cause: unknown) => {
        if (!signal.aborted) {
          setError(toError(cause));
          setPhase('ready');
        }
      },
    );
    return () => controller.abort();
  }, [uri, open, generation]);

  async function save(change?: {
    entries: FileBrowserAclEntry[];
    mode: 'inherit' | 'restricted';
  }) {
    const controller = lifetime.current;
    if (
      pending.current ||
      latest.current.readOnly ||
      !report ||
      !controller ||
      controller.signal.aborted
    )
      return;
    pending.current = true;
    setPhase('saving');
    setError(undefined);
    setSaved(false);
    const { client, requestOptions } = latest.current.context;
    const signal = requestOptions.signal
      ? AbortSignal.any([controller.signal, requestOptions.signal])
      : controller.signal;
    try {
      const value = await writeAcl(
        client,
        uri,
        { ...requestOptions, signal },
        change && { direct_entries: change.entries, mode: change.mode },
      );
      if (signal.aborted) return;
      setReport(value);
      setConfirmation(undefined);
      setSaved(true);
      latest.current.onUpdated(value);
    } catch (cause) {
      if (!signal.aborted) setError(toError(cause));
    } finally {
      if (!controller.signal.aborted && lifetime.current === controller) {
        pending.current = false;
        setPhase('ready');
      }
    }
  }

  function change(entries: FileBrowserAclEntry[]) {
    if (!report || pending.current || latest.current.readOnly) return;
    const plan = planChange(report, normalizeEntries(entries));
    setSaved(false);
    setError(undefined);
    if (plan.confirmation)
      setConfirmation({ kind: 'restrict', entries: plan.entries });
    else void save(plan);
  }

  return {
    report,
    confirmation,
    error,
    phase,
    saved,
    retry: () => setGeneration((value) => value + 1),
    change,
    restore: () => {
      if (!pending.current && !latest.current.readOnly)
        setConfirmation({ kind: 'restore' });
    },
    cancel: () => {
      if (!pending.current) {
        setConfirmation(undefined);
        setError(undefined);
      }
    },
    confirm: () => {
      if (confirmation?.kind === 'restore') void save();
      else if (confirmation)
        void save({ entries: confirmation.entries, mode: 'restricted' });
    },
  };
}
export type AclModel = ReturnType<typeof useAclReport>;
