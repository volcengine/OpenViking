import { useRef, useState } from 'react';

import type { FilePreviewFileTab, FilePreviewTab } from './types.js';

/**
 * 文件页签的编辑态：进入编辑时向宿主取完整文本，保存成功才退出编辑。
 *
 * 进入编辑前用请求号作废旧请求，避免快速切换页签时迟到的结果写进新草稿；
 * 保存期间拦住重复提交与页签切换，失败时保留草稿。
 *
 * Editing state of a file tab. The full text is requested when editing starts,
 * and the draft only leaves editing after a successful save. A request id
 * invalidates stale loads when the tab changes quickly; duplicate saves and tab
 * switches are blocked while saving, and a failed save keeps the draft.
 */
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
    const wasEditing = editing;
    const discarded = draft;
    setEditing(false);
    setLoadingEdit(false);
    setEditFailed(false);
    setSaveFailed(false);
    setDraft('');
    if (wasEditing && tab.kind === 'file') tab.editing?.onCancel?.(discarded);
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

  /** 保存草稿，成功返回 `true`，失败保留草稿并返回 `false`。 */
  async function save(tab: FilePreviewFileTab): Promise<boolean> {
    if (!editing || !tab.editing || pendingSave.current) return false;
    pendingSave.current = true;
    setSaving(true);
    setSaveFailed(false);
    const value = draft;
    try {
      await tab.editing.save(value);
    } catch {
      setSaveFailed(true);
      return false;
    } finally {
      pendingSave.current = false;
      setSaving(false);
    }
    setEditing(false);
    setDraft('');
    tab.editing.onSave?.(value);
    return true;
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
