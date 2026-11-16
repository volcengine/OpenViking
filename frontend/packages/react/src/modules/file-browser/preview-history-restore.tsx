import InfoIcon from '../../../../icons/svg/file-preview/history-info.svg?react';
import { historyVersionLabel } from './preview-history-controls.js';
import RollbackIcon from '../../../../icons/svg/file-preview/rollback.svg?react';
import { useRef, useState } from 'react';
import { OV_PATHS } from '../../client/paths.js';
import { isOvClientError } from '../../client/errors.js';
import { PreviewMutationError } from './preview-query-state.js';
import { Button } from '../../shared/primitives/button/button.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
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
  onConflict,
  ...props
}: PreviewWorkflowProps & {
  selected?: PreviewHistoryEntry;
  current?: PreviewHistoryEntry;
  disabled: boolean;
  onRestored: (unchanged: boolean) => void;
  onConflict: () => void;
}) {
  const [confirming, setConfirming] = useState<PreviewHistoryEntry>();
  const mutation = usePreviewMutation(props.uri, props.requestOptions);
  const anchor = useRef<HTMLButtonElement>(null);
  const { text } = props;
  return (
    <>
      {selected || confirming ? (
        <Button
          ref={anchor}
          icon={<RollbackIcon width={14} height={14} aria-hidden="true" />}
          size="small"
          type="outline"
          disabled={
            disabled || !current || !selected || selected.id === current.id
          }
          onClick={() => {
            mutation.reset();
            setConfirming(selected);
          }}
        >
          {text.rollback}
        </Button>
      ) : null}
      {confirming ? (
        <Modal
          open
          title={text.rollbackTitle}
          locale={props.locale}
          triggerRef={anchor}
          className="ov-file-browser-workflow ov-file-history-restore"
          onClose={
            mutation.pending ? undefined : () => setConfirming(undefined)
          }
        >
          <h2>
            <InfoIcon width={20} height={20} aria-hidden="true" />
            {text.rollbackTitle}
          </h2>
          <p>{text.rollbackHint}</p>
          <p className="ov-file-browser-workflow__uri">
            {props.uri} · {historyVersionLabel(confirming, props.locale)}
          </p>
          <PreviewMutationError
            error={mutation.error}
            title={text.failureTitle(text.rollbackConfirm)}
            locale={props.locale}
          />
          <div className="ov-file-browser-workflow__actions">
            <Button
              disabled={mutation.pending}
              onClick={() => setConfirming(undefined)}
            >
              {text.cancel}
            </Button>
            <Button
              type="primary"
              loading={mutation.pending}
              disabled={!current}
              onClick={() => {
                if (!current) return;
                let unchanged = false;
                void mutation.run(
                  async (api, options) => {
                    const result = await api
                      .request<{ result?: string }>({
                        method: 'POST',
                        path: OV_PATHS.snapshotRestore,
                        body: {
                          path: props.uri,
                          restore_commit_id: confirming.snapshot,
                          current_commit_id: current.snapshot,
                        },
                        options,
                      })
                      .catch((error: unknown) => {
                        if (isOvClientError(error) && error.code === 'CONFLICT')
                          onConflict();
                        throw error;
                      });
                    unchanged = result.result === 'noop';
                  },
                  () => {
                    setConfirming(undefined);
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
