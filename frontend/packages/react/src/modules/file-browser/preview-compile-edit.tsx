import { useToast } from '../../shared/primitives/toast/use-toast.js';
import { PreviewLoading, PreviewMutationError } from './preview-query-state.js';
import { useRef, useState } from 'react';
import { OV_PATHS } from '../../client/paths.js';
import { PreviewCompilePicker } from './preview-compile-picker.js';
import type { ContextPickerEntry } from '../../components/context-picker/types.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import { Button } from '../../shared/primitives/button/button.js';
import { usePreviewMutation } from './use-preview-mutation.js';
import { displayNameFromUri } from '../../shared/directory-tree/rows.js';
import {
  type CompileScheduleDraft,
  PreviewCompileSchedule,
} from './preview-compile-schedule.js';
import type { CompileField } from './preview-compile-config.js';
import type {
  CompileConfig,
  PreviewWorkflowProps,
} from './preview-workflow-types.js';

export function PreviewCompileEdit({
  initial,
  field,
  onCancel,
  onSaved,
  ...props
}: PreviewWorkflowProps & {
  canWrite: boolean;
  initial: CompileConfig;
  loadingText: string;
  field: CompileField;
  onCancel: () => void;
  onSaved: (value: CompileConfig) => void;
}) {
  const { text } = props;
  const [toast, toastHolder] = useToast({ locale: props.locale });
  const initialSchedule = initial.auto_update ? initial.update_schedule : null;
  const [schedule, setSchedule] = useState<CompileScheduleDraft>({
    mode: initialSchedule?.frequency ?? 'none',
    time: initialSchedule
      ? `${String(initialSchedule.hour).padStart(2, '0')}:${String(initialSchedule.minute).padStart(2, '0')}`
      : '',
    weekday: initialSchedule?.weekday,
  });
  const [invalidWeekday, setInvalidWeekday] = useState(false);
  const [selection, setSelection] = useState<ContextPickerEntry[]>(
    (field === 'from' ? initial.from : [initial.skill]).map((uri) => ({
      uri,
      name: displayNameFromUri(uri),
      type: 'directory',
    })),
  );
  const anchor = useRef<HTMLElement>(null);
  const mutation = usePreviewMutation(props.uri, props.requestOptions);
  const save = (items = selection) => {
    if (!props.canWrite) return;
    const next = {
      ...initial,
      ...(field === 'from' ? { from: items.map((item) => item.uri) } : {}),
      ...(field === 'skill'
        ? { skill: items[0]!.uri.replace(/\/SKILL\.md$/, '') }
        : {}),
      ...(field === 'update_schedule'
        ? {
            auto_update: schedule.mode !== 'none',
            update_schedule:
              schedule.mode === 'none'
                ? null
                : {
                    frequency: schedule.mode,
                    hour: Number(schedule.time.split(':')[0]),
                    minute: Number(schedule.time.split(':')[1]),
                    ...(schedule.mode === 'weekly'
                      ? { weekday: schedule.weekday }
                      : {}),
                  },
          }
        : {}),
    };
    if (!next.from.length || !next.skill.trim()) {
      toast.warning({
        id: 'compile-validation',
        content: !next.from.length ? text.sourcesRequired : text.skillRequired,
      });
      return;
    }
    toast.clear();
    void mutation.run(
      (api, options) =>
        api.request({
          method: 'POST',
          path: OV_PATHS.compileConfigUpdate,
          body: {
            ...(next.compile_name
              ? { compile_name: next.compile_name, args: next.args }
              : {}),
            to: next.to,
            from: next.from,
            skill: next.skill,
            instruction: next.instruction,
            auto_update: next.auto_update,
            update_schedule: next.update_schedule,
          },
          options,
        }),
      () => onSaved(next),
      'file_browser_compile_config_save',
    );
  };
  const title =
    field === 'from'
      ? text.selectSources
      : field === 'skill'
        ? text.selectSkill
        : text.editSchedule;
  const error = (
    <PreviewMutationError
      error={mutation.error}
      title={text.failureTitle(text.save)}
      locale={props.locale}
    />
  );
  return (
    <>
      {' '}
      {toastHolder}{' '}
      {field === 'update_schedule' ? (
        <Modal
          open
          title={title}
          locale={props.locale}
          className="ov-file-browser-workflow ov-compile-config"
          triggerRef={anchor}
          onClose={mutation.pending ? undefined : onCancel}
        >
          <h2>{title}</h2>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (schedule.mode === 'weekly' && !schedule.weekday) {
                setInvalidWeekday(true);
                return;
              }
              save();
            }}
          >
            <fieldset disabled={mutation.pending}>
              <legend className="ov-compile-config__legend">
                {text.schedule}
              </legend>
              <PreviewCompileSchedule
                value={schedule}
                disabled={mutation.pending}
                invalid={invalidWeekday}
                text={text}
                locale={props.locale}
                onChange={(value) => {
                  setSchedule(value);
                  setInvalidWeekday(false);
                  mutation.reset();
                }}
              />
            </fieldset>
            {error}
            <div className="ov-file-browser-workflow__actions">
              <Button disabled={mutation.pending} onClick={onCancel}>
                {text.cancel}
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                loading={mutation.pending}
              >
                {text.save}
              </Button>
            </div>
          </form>
        </Modal>
      ) : (
        <PreviewCompilePicker
          open
          loadingFallback={<PreviewLoading label={props.loadingText} />}
          title={title}
          locale={props.locale}
          value={selection}
          multiple={field === 'from'}
          field={field}
          requestOptions={props.requestOptions}
          error={error}
          confirming={mutation.pending}
          onChange={(items) => {
            setSelection(items);
            mutation.reset();
          }}
          onCancel={onCancel}
          onConfirm={(items) => save([...items])}
        />
      )}{' '}
    </>
  );
}
