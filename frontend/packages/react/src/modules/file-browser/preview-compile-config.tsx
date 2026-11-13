import { TextArea, Input } from '../../shared/primitives/input/input.js';
import { Checkbox } from '../../shared/primitives/checkbox/checkbox.js';
import { useCallback, useId, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { OV_PATHS } from '../../client/paths.js';
import { Button } from '../../shared/primitives/button/button.js';
import { readCompileConfig } from './preview-native.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { usePreviewMutation } from './use-preview-mutation.js';
import {
  PreviewMutationError,
  PreviewQueryState,
} from './preview-query-state.js';
import { PreviewCompileSchedule } from './preview-compile-schedule.js';
import type {
  CompileConfig,
  PreviewWorkflowProps,
} from './preview-workflow-types.js';

type Props = PreviewWorkflowProps & {
  canWrite: boolean;
  loadingText: string;
  retryText: string;
};
export function PreviewCompileConfig(props: Props) {
  const { uri, text, requestOptions, loadingText, retryText } = props;
  const query = useDashboardQuery(
    useCallback(
      (client: OvClient, options: OvRequestOptions) =>
        readCompileConfig(client, uri, options),
      [uri],
    ),
    requestOptions,
  );
  return (
    <>
      <PreviewQueryState
        query={query}
        text={{ ...text, loading: loadingText, retry: retryText }}
      />
      {query.data ? (
        <CompileConfigForm {...props} initial={query.data} />
      ) : null}
    </>
  );
}
function CompileConfigForm({
  uri,
  initial,
  text,
  locale,
  canWrite,
  requestOptions,
  onUpdated,
  onClose,
}: Props & { initial: CompileConfig }) {
  const id = useId();
  const [draft, setDraft] = useState(initial);
  const [sources, setSources] = useState(initial.from.join('\n'));
  const mutation = usePreviewMutation(uri, requestOptions);
  const change = (value: Partial<CompileConfig>) => {
    setDraft((current) => ({ ...current, ...value }));
    mutation.reset();
  };
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (!canWrite) return;
        const body = {
          to: uri,
          from: sources
            .split('\n')
            .map((value) => value.trim())
            .filter(Boolean),
          skill: draft.skill,
          instruction: draft.instruction,
          auto_update: draft.auto_update,
          update_schedule: draft.update_schedule,
        };
        void mutation.run(
          (client, options) =>
            client.request({
              method: 'POST',
              path: OV_PATHS.compileConfigUpdate,
              body,
              options,
            }),
          onUpdated,
        );
      }}
    >
      <fieldset disabled={!canWrite || mutation.pending}>
        <label htmlFor={`${id}-sources`}>{text.sources}</label>
        <TextArea
          id={`${id}-sources`}
          required
          value={sources}
          onChange={(event) => {
            setSources(event.target.value);
            mutation.reset();
          }}
        />
        <label htmlFor={`${id}-skill`}>{text.skill}</label>
        <Input
          id={`${id}-skill`}
          required
          value={draft.skill}
          onChange={(event) => change({ skill: event.target.value })}
        />
        <label htmlFor={`${id}-instruction`}>{text.instruction}</label>
        <TextArea
          id={`${id}-instruction`}
          value={draft.instruction ?? ''}
          onChange={(event) => change({ instruction: event.target.value })}
        />
        <Checkbox
          id={`${id}-automatic`}
          checked={draft.auto_update}
          onChange={(next) => change({ auto_update: next })}
        >
          {text.automatic}
        </Checkbox>
        <PreviewCompileSchedule
          value={draft.update_schedule}
          text={text}
          locale={locale}
          onChange={(update_schedule) => change({ update_schedule })}
        />
      </fieldset>
      <PreviewMutationError error={mutation.error} text={text} />
      {mutation.succeeded ? <p role="status">{text.saved}</p> : null}
      <div className="ov-file-browser-workflow__actions">
        <Button size="small" htmlType="button" onClick={onClose}>
          {text.cancel}
        </Button>
        {canWrite ? (
          <Button
            size="small"
            type="primary"
            htmlType="submit"
            loading={mutation.pending}
          >
            {text.save}
          </Button>
        ) : null}
      </div>
    </form>
  );
}
