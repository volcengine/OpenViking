import { useRef, useState } from 'react';

import type { FilePreviewFileTab, FilePreviewTab } from './types.js';

export function useFilePreviewEditing() {
  const [editing, setEditing] = useState(false);
  const [loadingEdit, setLoadingEdit] = useState(false);
  const [editFailed, setEditFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [draft, setDraft] = useState('');
  const requestId = useRef(0);
  const loading = useRef(false);
  const pendingSave = useRef(false);

  function discard(tab: FilePreviewTab) {
    requestId.current += 1;
    loading.current = false;
    setEditing(false);
    setLoadingEdit(false);
    setEditFailed(false);
    setSaveFailed(false);
    setDraft('');
    if (editing && tab.kind === 'file') tab.editing?.onCancel?.(draft);
  }

  async function start(tab: FilePreviewFileTab) {
    if (loading.current || pendingSave.current) return;
    const id = ++requestId.current;
    loading.current = true;
    setLoadingEdit(true);
    setEditFailed(false);
    setSaveFailed(false);
    let content: string | undefined;
    try {
      content = tab.editing?.loadContent
        ? await tab.editing.loadContent(tab.source)
        : (tab.source.content ?? (await tab.source.file?.text()));
    } catch {
      if (id === requestId.current) setEditFailed(true);
    }
    if (id !== requestId.current) return;
    loading.current = false;
    setLoadingEdit(false);
    if (content !== undefined) {
      setDraft(content);
      setEditing(true);
    }
  }

  async function save(tab: FilePreviewFileTab) {
    if (!editing || !tab.editing || pendingSave.current) return;
    pendingSave.current = true;
    setSaving(true);
    setSaveFailed(false);
    const value = draft;
    try {
      await tab.editing.save(value);
    } catch {
      setSaveFailed(true);
      return;
    } finally {
      pendingSave.current = false;
      setSaving(false);
    }
    setEditing(false);
    setDraft('');
    tab.editing.onSave?.(value);
  }

  return {
    editing,
    loadingEdit,
    editFailed,
    saving,
    saveFailed,
    draft,
    discard,
    start,
    save,
    updateDraft: (value: string) => {
      setDraft(value);
      setSaveFailed(false);
    },
  };
}
