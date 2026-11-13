import { useEffect, useRef, useState } from 'react';

import type { FilePreviewFileTab, FilePreviewTab } from './types.js';

/**
 * 文件页签的编辑态：进入编辑时向宿主取完整文本，保存成功才退出编辑。
 *
 * 进入编辑前用请求号作废旧请求，避免快速切换页签时迟到的结果写进新草稿；
 * 保存期间拦住重复提交与页签切换，失败时保留草稿。
 *
 * 受控模式下宿主可以直接改 `activeKey`，不经过 `switchTab`；因此这里比对页签
 * key，变化时在渲染期间就把草稿丢掉，否则会拿上一个页签的草稿去保存新页签。
 *
 * Editing state of a file tab. The full text is requested when editing starts,
 * and the draft only leaves editing after a successful save. A request id
 * invalidates stale loads when the tab changes quickly; duplicate saves and tab
 * switches are blocked while saving, and a failed save keeps the draft.
 *
 * Under a controlled `activeKey` the host can switch tabs without going through
 * `switchTab`, so the hook compares the active key and drops the draft during
 * render when it changes — otherwise the previous tab's draft would be saved to
 * the new one.
 */
export function useFilePreviewEditing(activeTabKey: string) {
  const [editing, setEditing] = useState(false);
  const [loadingEdit, setLoadingEdit] = useState(false);
  const [editFailed, setEditFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [versionNotCreated, setVersionNotCreated] = useState(false);
  const [versionNoteKey, setVersionNoteKey] = useState<string>();
  const noteRequest = useRef<{
    key: string;
    resolve: (note: string | undefined) => void;
  }>(undefined);
  const [draft, setDraft] = useState('');
  const requestId = useRef(0);
  const loading = useRef(false);
  const pendingSave = useRef(false);
  // 上一次渲染时的页签 key。变化说明换了文件，草稿必须作废——在渲染期间直接
  // 处理，不放进 Effect，避免先渲染一帧旧草稿。
  const lastKey = useRef(activeTabKey);

  if (lastKey.current !== activeTabKey) {
    lastKey.current = activeTabKey;
    requestId.current += 1;
    loading.current = false;
    if (editing) setEditing(false);
    if (loadingEdit) setLoadingEdit(false);
    if (editFailed) setEditFailed(false);
    if (saveFailed) setSaveFailed(false);
    if (versionNotCreated) setVersionNotCreated(false);
    if (draft) setDraft('');
  }

  // Resolve a pending save confirmation when its tab goes away or unmounts.
  useEffect(
    () => () => {
      if (noteRequest.current?.key !== activeTabKey) return;
      noteRequest.current.resolve(undefined);
      noteRequest.current = undefined;
    },
    [activeTabKey],
  );

  function finishVersionNote(note?: string) {
    noteRequest.current?.resolve(note);
    noteRequest.current = undefined;
    setVersionNoteKey(undefined);
  }

  function discard(tab: FilePreviewTab) {
    finishVersionNote();
    requestId.current += 1;
    loading.current = false;
    const wasEditing = editing;
    const discarded = draft;
    setEditing(false);
    setLoadingEdit(false);
    setEditFailed(false);
    setSaveFailed(false);
    setVersionNotCreated(false);
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
    setVersionNotCreated(false);
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
    if (!editing || !tab.editing || pendingSave.current || noteRequest.current)
      return false;
    const capability = tab.editing;
    const createVersion = capability.createVersion;
    const id = requestId.current;
    const note = createVersion
      ? await new Promise<string | undefined>((resolve) => {
          noteRequest.current = { key: activeTabKey, resolve };
          setVersionNoteKey(activeTabKey);
        })
      : undefined;
    if ((createVersion && note === undefined) || id !== requestId.current)
      return false;
    pendingSave.current = true;
    setSaving(true);
    setSaveFailed(false);
    const value = draft;
    let versionCreated = true;
    try {
      await capability.save(value);
      if (createVersion) {
        try {
          versionCreated = await createVersion(note ?? '');
        } catch {
          versionCreated = false;
        }
      }
    } catch {
      if (id === requestId.current) setSaveFailed(true);
      return false;
    } finally {
      pendingSave.current = false;
      setSaving(false);
    }
    const current = id === requestId.current;
    if (current) {
      setEditing(false);
      setDraft('');
      setVersionNotCreated(!versionCreated);
    }
    capability.onSave?.(value);
    return current;
  }

  return {
    editing,
    loadingEdit,
    editFailed,
    saving,
    saveFailed,
    versionNotCreated,
    versionNoteOpen: versionNoteKey === activeTabKey && editing,
    finishVersionNote,
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
