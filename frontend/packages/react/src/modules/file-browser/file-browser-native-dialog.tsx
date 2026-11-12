import { useState, type RefObject } from 'react';
import { Modal } from '../../shared/primitives/modal/modal.js';
import { PreviewHistory } from './preview-history.js';
import { PreviewCompileConfig } from './preview-compile-config.js';
import { PreviewPackImport } from './preview-pack-import.js';
import type { PreviewWorkflowProps } from './preview-workflow-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

export function FileBrowserNativeDialog({
  kind,
  text,
  triggerRef,
  canWrite,
  versioned,
  showContent,
  ...props
}: Omit<PreviewWorkflowProps, 'text'> & {
  kind: 'history' | 'importPack' | 'configure';
  text: ResolvedLabels;
  triggerRef: RefObject<HTMLElement | null>;
  canWrite: boolean;
  versioned: boolean;
  showContent: boolean;
}) {
  const title = kind === 'configure' ? text.workflow.configure : text[kind];
  const common = { ...props, text: text.workflow };
  const [historyBusy, setHistoryBusy] = useState(false);
  return (
    <Modal
      open
      title={title}
      locale={props.locale}
      triggerRef={triggerRef}
      onClose={historyBusy ? undefined : props.onClose}
      className={
        kind === 'history'
          ? 'ov-file-browser-workflow ov-file-history'
          : 'ov-file-browser-workflow'
      }
    >
      <h2>{title}</h2>
      {kind === 'history' ? null : (
        <p className="ov-file-browser-workflow__uri">{props.uri}</p>
      )}
      {kind === 'history' ? (
        <PreviewHistory
          {...common}
          versioned={versioned}
          showContent={showContent}
          canWrite={canWrite}
          onRestoreBusyChange={setHistoryBusy}
          loadingText={text.loading}
          retryText={text.retry}
        />
      ) : kind === 'importPack' ? (
        <PreviewPackImport {...common} canWrite={canWrite} />
      ) : (
        <PreviewCompileConfig
          {...common}
          canWrite={canWrite}
          loadingText={text.loading}
          retryText={text.retry}
        />
      )}
    </Modal>
  );
}
