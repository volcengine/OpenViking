import InfoIcon from '../../../../icons/svg/file-preview/history-info.svg?react';
import { historyVersionLabel } from './preview-history-controls.js';
import RollbackIcon from '../../../../icons/svg/file-preview/rollback.svg?react';
import { useRef, useState } from 'react';
import { OV_PATHS } from '../../client/paths.js';
import { Button } from '../../shared/primitives/button/button.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import { PreviewMutationError } from './preview-query-state.js';
import { usePreviewMutation } from './use-preview-mutation.js';
import type {
  PreviewHistoryEntry,
  PreviewWorkflowProps,
} from './preview-workflow-types.js';

export function PreviewHistoryRestore({
  selected,
  current,
  disabled,
  onRestored,
  ...props
}: PreviewWorkflowProps & {
  selected: PreviewHistoryEntry;
  current?: PreviewHistoryEntry;
  disabled: boolean;
  onRestored: (unchanged: boolean) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const mutation = usePreviewMutation(props.uri, props.requestOptions);
  const { text } = props;
  return (
    <>
      <Button
        ref={anchor}
        icon={<RollbackIcon width={14} height={14} aria-hidden="true" />}
        size="small"
        type="outline"
        disabled={disabled || !current || selected.id === current.id}
        onClick={() => {
          mutation.reset();
          setConfirming(true);
        }}
      >
        {text.rollback}
      </Button>
      {confirming ? (
        <Modal
          open
          title={text.rollbackTitle}
          locale={props.locale}
          triggerRef={anchor}
          className="ov-file-browser-workflow ov-file-history-restore"
          onClose={mutation.pending ? undefined : () => setConfirming(false)}
        >
          <h2>
            <InfoIcon width={20} height={20} aria-hidden="true" />
            {text.rollbackTitle}
          </h2>
          <p>{text.rollbackHint}</p>
          <p className="ov-file-browser-workflow__uri">
            {props.uri} · {historyVersionLabel(selected, props.locale)}
          </p>
          <PreviewMutationError
            error={mutation.error}
            title={text.failureTitle(text.rollbackConfirm)}
            locale={props.locale}
          />
          <div className="ov-file-browser-workflow__actions">
            <Button
              disabled={mutation.pending}
              onClick={() => setConfirming(false)}
            >
              {text.cancel}
            </Button>
            <Button
              type="primary"
              loading={mutation.pending}
              onClick={() => {
                if (!current) return;
                let unchanged = false;
                void mutation.run(
                  async (api, options) => {
                    const result = await api.request<{ result?: string }>({
                      method: 'POST',
                      path: OV_PATHS.snapshotRestore,
                      body: {
                        path: props.uri,
                        restore_commit_id: selected.snapshot,
                        current_commit_id: current.snapshot,
                      },
                      options,
                    });
                    unchanged = result.result === 'noop';
                  },
                  () => {
                    setConfirming(false);
                    onRestored(unchanged);
                  },
                );
              }}
            >
              {text.rollbackConfirm}
            </Button>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
