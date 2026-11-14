import { traceLink } from '../../shared/monitoring/trace/link.js';
import { opaqueIdentity } from '../../shared/monitoring/clock.js';
import { scopeBinding } from '../../shared/monitoring/binding.js';
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { FileViewerState } from '@ark-design/ui/file-viewer/core';
import {
  useMonitoringBinding,
  useRenderedMonitoring,
} from '../../shared/monitoring/context.js';
import { useMonitoringFlow } from '../../shared/monitoring/flow-context.js';
import {
  workflowForBinding,
  type Workflow,
} from '../../shared/monitoring/workflow.js';
import { isEmptyFile } from './file-preview-source.js';
import { previewContentKind } from './media-kind.js';
import type { FilePreviewBodyProps } from './file-preview-body.js';

type Attempt = {
  identity: object;
  sourceIdentity: object;
  generation: number;
  available: boolean;
  operation?: Workflow<'file_preview_content_prepare'>;
};
export function useContentMonitoring(props: FilePreviewBodyProps) {
  const binding = useMonitoringBinding();
  const enabled = useRenderedMonitoring();
  const flow = useMonitoringFlow();
  const source = props.fileTab.source;
  const [retry, setRetry] = useState(0);
  const [overlay, setOverlay] = useState<{
    identity: object;
    loading: boolean;
  }>();
  const sourceIdentity = useMemo(
    () =>
      opaqueIdentity(
        props.fileTab.key,
        props.viewMode,
        source.path,
        source.fileName,
        source.fileType,
        source.content,
        source.url,
        source.file,
      ),
    [
      props.fileTab.key,
      source.path,
      source.fileName,
      source.fileType,
      source.content,
      source.url,
      source.file,
      props.viewMode,
    ],
  );
  const identity = useMemo(
    () => opaqueIdentity(sourceIdentity, retry),
    [sourceIdentity, retry],
  );
  const available =
    !props.fileTab.loading &&
    !props.editing.editing &&
    !props.editing.loadingEdit &&
    !props.editing.editFailed;
  const attempt = useRef<Attempt | undefined>(undefined);
  const [outcome, setOutcome] = useState<{
    identity: object;
    state: FileViewerState;
    error?: unknown;
  }>();
  const observe = useCallback(
    (state: FileViewerState, error?: unknown, reason?: 'retry') => {
      if (enabled) {
        if (reason === 'retry') setRetry((value) => value + 1);
        setOutcome((previous) =>
          previous?.identity === identity &&
          previous.state === state &&
          previous.error === error
            ? previous
            : { identity, state, error },
        );
      }
    },
    [identity, enabled],
  );
  const preview =
    flow?.operation?.target === 'file_browser_preview_load' &&
    flow.operation.identity === (props.monitoringTargetKey ?? props.fileTab.key)
      ? flow.operation
      : undefined;
  const failed = useCallback(
    (error: unknown, operation = attempt.current?.operation) => {
      operation?.fail(error, 'renderer');
      preview?.fail(error, 'renderer');
    },
    [preview],
  );
  useLayoutEffect(() => {
    const prior = attempt.current;
    const changed =
      !prior ||
      prior.identity !== identity ||
      prior.operation?.span.node.status === 'cancelled' ||
      (!prior.available && available);
    if (changed) {
      prior?.operation?.cancel();
      attempt.current = {
        identity,
        sourceIdentity,
        generation: retry,
        available,
      };
      if (available && enabled && binding) {
        const scoped = scopeBinding(
          binding,
          props.viewMode === 'source' ? 'source' : 'content',
          flow?.operation?.span ?? binding.parent,
          flow?.spans ?? binding.related,
        );
        const previous = prior?.operation?.span;
        const isRetry =
          prior?.sourceIdentity === sourceIdentity &&
          prior.generation !== retry;
        attempt.current.operation = workflowForBinding(
          scoped,
          'file_preview_content_prepare',
          undefined,
          {
            trigger: isRetry ? 'retry' : undefined,
            retryOf:
              isRetry && previous?.available()
                ? traceLink(
                    previous.snapshot(),
                    previous.trace.spanId,
                    'retry-of',
                  )
                : undefined,
          },
        );
        if (isRetry && !previous?.available())
          attempt.current.operation?.span.gap('started-before-enabled');
      }
    } else if (!available) {
      prior.operation?.cancel();
      prior.available = false;
    }
    const operation = attempt.current?.operation;
    if (!operation || !available || props.reports === false) return;
    const current = outcome?.identity === identity ? outcome : undefined;
    const empty = isEmptyFile(source);
    if (current?.state === 'error') {
      const failure = current.error ?? new Error('Preview preparation failed');
      failed(failure, operation);
      return;
    }
    if (overlay?.identity === identity && overlay.loading) return;
    if (!empty && current?.state !== 'ready') return;
    operation.ready({ empty, viewMode: props.viewMode });
    preview?.ready({ contentKind: previewContentKind(source), empty });
  }, [
    binding,
    enabled,
    flow,
    identity,
    sourceIdentity,
    available,
    retry,
    overlay,
    outcome,
    failed,
    preview,
    props.reports,
    props.viewMode,
    source,
  ]);
  useLayoutEffect(
    () => () => {
      // Retain settled attempts across development effect replay; only cancelled work restarts.
      attempt.current?.operation?.cancel();
    },
    [],
  );
  return {
    observe,
    caught: (error: unknown) => failed(error),
    operation: attempt.current?.operation,
    reset: () => {
      if (enabled) setRetry((value) => value + 1);
    },
    loading: (loading: boolean) => {
      if (enabled)
        setOverlay((previous) =>
          previous?.identity === identity && previous.loading === loading
            ? previous
            : { identity, loading },
        );
    },
  };
}
