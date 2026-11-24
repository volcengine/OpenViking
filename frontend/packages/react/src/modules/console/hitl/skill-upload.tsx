import { useEffect, useRef, useState, type RefObject } from 'react';
import { OV_PATHS } from '../../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../../client/types.js';
import type { ContextPickerEntry } from '../../../components/context-picker/index.js';
import { useLocale } from '../../../shared/locale-context.js';
import { Button } from '../../../shared/primitives/button/button.js';
import { Modal } from '../../../shared/primitives/modal/modal.js';
import { useToast } from '../../../shared/primitives/toast/use-toast.js';
import { hitlLabels } from './locales.js';
import { parseCompileUri } from './uris.js';

type SkillResult = { root_uri?: string; uri?: string };

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
  const [toast, feedback] = useToast({ locale });
  const upload = useRef<AbortController>(undefined);
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState(false);
  // The file read and upload outlive the click handler. Closing the card or
  // changing scope unmounts this dialog and must cancel its pending upload.
  useEffect(() => () => upload.current?.abort(), []);
  const choose = (items?: FileList | null) => {
    if (upload.current) return;
    const chosen =
      items?.length === 1 && items[0]?.name === 'SKILL.md'
        ? items[0]
        : undefined;
    setFile(chosen);
    toast.clear();
    if (items?.length && !chosen) {
      toast.error({ content: labels.skillFileInvalid });
    }
  };
  async function submit() {
    if (!file || upload.current) return;
    const controller = new AbortController();
    upload.current = controller;
    const signal = options?.signal
      ? AbortSignal.any([controller.signal, options.signal])
      : controller.signal;
    setBusy(true);
    toast.clear();
    try {
      let data: string;
      try {
        data = await file.text();
      } catch {
        if (!signal.aborted)
          toast.error({ content: labels.skillFileUnreadable });
        return;
      }
      if (signal.aborted) return;
      let result: SkillResult;
      try {
        result = await client.request<SkillResult>({
          method: 'POST',
          path: OV_PATHS.skills,
          body: { data, wait: true },
          options: { ...options, signal },
        });
      } catch {
        // The scoped client publishes the original request error once.
        return;
      }
      if (signal.aborted) return;
      const value = result?.root_uri ?? result?.uri;
      const root =
        typeof value === 'string' ? parseCompileUri(value) : undefined;
      if (!root) {
        toast.error({ content: labels.skillUploadMissingPath });
        return;
      }
      const uri = root.endsWith('/SKILL.md') ? root : root + '/SKILL.md';
      onUploaded({ uri, name: 'SKILL.md', type: 'file' });
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
      {feedback}
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
