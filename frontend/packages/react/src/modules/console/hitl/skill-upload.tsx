import { useEffect, useRef, useState, type RefObject } from 'react';
import { OV_PATHS } from '../../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../../client/types.js';
import type { ContextPickerEntry } from '../../../components/context-picker/index.js';
import { useLocale } from '../../../shared/locale-context.js';
import { Button } from '../../../shared/primitives/button/button.js';
import { Modal } from '../../../shared/primitives/modal/modal.js';
import { hitlLabels } from './locales.js';

export function SkillUpload({
  client,
  options,
  triggerRef,
  onCancel,
  onUploaded,
}: {
  client: OvClient;
  options?: OvRequestOptions;
  triggerRef: RefObject<HTMLElement | null>;
  onCancel: () => void;
  onUploaded: (entry: ContextPickerEntry) => void;
}) {
  const locale = useLocale();
  const labels = hitlLabels(locale);
  const upload = useRef<AbortController>(undefined);
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => () => upload.current?.abort(), []);
  const choose = (items?: FileList | null) => {
    if (upload.current) return;
    const chosen =
      items?.length === 1 && items[0]?.name === 'SKILL.md'
        ? items[0]
        : undefined;
    setFile(chosen);
    setError(Boolean(items?.length && !chosen));
  };
  async function submit() {
    if (!file || upload.current) return;
    const controller = new AbortController();
    upload.current = controller;
    const signal = options?.signal
      ? AbortSignal.any([controller.signal, options.signal])
      : controller.signal;
    setBusy(true);
    setError(false);
    try {
      const data = await file.text();
      signal.throwIfAborted();
      const result = await client.request<{ root_uri?: string; uri?: string }>({
        method: 'POST',
        path: OV_PATHS.skills,
        body: { data, wait: true },
        options: { ...options, signal },
      });
      signal.throwIfAborted();
      const root = (result.root_uri ?? result.uri)?.replace(/\/$/, '');
      if (!root?.startsWith('viking://')) throw new Error();
      const uri = root.endsWith('/SKILL.md') ? root : root + '/SKILL.md';
      onUploaded({ uri, name: 'SKILL.md', type: 'file' });
    } catch {
      if (!signal.aborted) setError(true);
    } finally {
      upload.current = undefined;
      if (!signal.aborted) setBusy(false);
    }
  }
  return (
    <Modal
      open
      locale={locale}
      title={labels.uploadSkill}
      triggerRef={triggerRef}
      onClose={busy ? undefined : onCancel}
      className="ov-viking-bot__compile-skill-upload"
    >
      <h2 className="ov-context-picker__title">{labels.uploadSkill}</h2>
      <div className="ov-context-picker__content">
        <label
          className="ov-viking-bot__compile-skill-drop"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            choose(event.dataTransfer.files);
          }}
        >
          <span>{file?.name ?? labels.chooseSkillFile}</span>
          <input
            type="file"
            accept=".md"
            disabled={busy}
            onChange={(event) => choose(event.currentTarget.files)}
          />
        </label>
        {error ? (
          <p role="alert" className="ov-context-picker__error">
            {file ? labels.skillUploadFailed : labels.skillFileInvalid}
          </p>
        ) : null}
      </div>
      <div className="ov-viking-bot__create-directory-actions">
        <Button disabled={busy} onClick={onCancel}>
          {labels.back}
        </Button>
        <Button
          type="primary"
          loading={busy}
          disabled={busy || !file}
          onClick={() => void submit()}
        >
          {labels.uploadSkill}
        </Button>
      </div>
    </Modal>
  );
}
