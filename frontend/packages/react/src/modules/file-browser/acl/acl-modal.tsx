import { Button } from '../../../shared/primitives/button/button.js';
import { useEffect, useId, useRef, type RefObject } from 'react';
import { Modal } from '../../../shared/primitives/modal/modal.js';
import type { Locale } from '../../../shared/locale.js';
import type { OvIdentityValue } from '../../../client/types.js';
import type {
  FileBrowserAclContext,
  FileBrowserAclOptions,
  FileBrowserAclReport,
} from '../acl-types.js';
import { AclAddPrincipal } from './acl-add-principal.js';
import { AclConfirmation } from './acl-confirmation.js';
import { AclEntryList } from './acl-entry-list.js';
import type { AclText } from './acl-labels.js';
import { useAclReport } from './use-acl-report.js';

export function AclModal({
  uri,
  open,
  text,
  locale,
  context,
  triggerRef,
  accountId,
  userId,
  options,
  onClose,
  onUpdated,
}: {
  uri: string;
  open: boolean;
  text: AclText;
  locale: Locale;
  context: FileBrowserAclContext;
  triggerRef: RefObject<HTMLButtonElement | null>;
  accountId?: OvIdentityValue;
  userId?: OvIdentityValue;
  options?: FileBrowserAclOptions;
  onClose: () => void;
  onUpdated: (report: FileBrowserAclReport) => void;
}) {
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const model = useAclReport({
    uri,
    context,
    open,
    readOnly: Boolean(options?.readOnly),
    onUpdated,
  });
  const disabled = Boolean(options?.readOnly) || model.phase !== 'ready';
  const entries = model.report?.effective_entries ?? [];
  const administratorId = options?.administratorUserId?.trim();
  const administrator = administratorId ? `user:${administratorId}` : undefined;
  const currentUserId = typeof userId === 'string' ? userId.trim() : undefined;
  const currentUser = currentUserId ? `user:${currentUserId}` : undefined;
  const wasConfirming = useRef(false);
  // useEffect 必要性：确认区卸载后将浏览器焦点放回仍存在的标题，
  // DOM 节点需提交后才可聚焦；每次确认切换重新判断，无订阅需要清理。
  useEffect(() => {
    if (wasConfirming.current && !model.confirmation) heading.current?.focus();
    wasConfirming.current = Boolean(model.confirmation);
  }, [model.confirmation]);
  return (
    <Modal
      id={id}
      open={open}
      title={text.title}
      closeLabel={text.close}
      triggerRef={triggerRef}
      className="ov-file-browser-acl"
      locale={locale}
      maskClosable={false}
      onClose={() => {
        if (model.phase === 'saving') return;
        if (model.confirmation) model.cancel();
        else onClose();
      }}
    >
      <h2 ref={heading} tabIndex={-1} className="ov-file-browser-acl__title">
        {text.title}
      </h2>
      {model.error ? (
        <div className="ov-file-browser-acl__failure" role="alert">
          <p>{model.report ? text.saveFailed : text.loadFailed}</p>
          <p>{model.error.message}</p>
          {!model.report ? (
            <Button
              size="small"
              type="default"
              htmlType="button"
              onClick={model.retry}
            >
              {text.retry}
            </Button>
          ) : null}
        </div>
      ) : null}
      <div role="status" className="ov-file-browser-acl__status">
        {model.phase === 'saving'
          ? text.saving
          : model.phase === 'loading'
            ? text.loading
            : model.saved
              ? text.saved
              : ''}
      </div>
      {model.confirmation ? (
        <AclConfirmation model={model} text={text} />
      ) : model.report ? (
        <>
          {model.report.acl_mode === 'restricted' ? (
            <div className="ov-file-browser-acl__restricted">
              <p>{text.restricted}</p>
              <Button
                size="small"
                type="default"
                htmlType="button"
                disabled={disabled}
                onClick={model.restore}
              >
                {text.restore}
              </Button>
            </div>
          ) : null}
          {options?.readOnly ? (
            <p className="ov-file-browser-acl__hint">{text.readOnly}</p>
          ) : null}
          <AclAddPrincipal
            entries={entries}
            administratorId={administratorId}
            text={text}
            disabled={disabled}
            context={context}
            accountId={accountId}
            options={options}
            onAdd={(added) => model.change([...entries, ...added])}
          />
          <AclEntryList
            report={model.report}
            administrator={administrator}
            currentUser={currentUser}
            disabled={disabled}
            saving={model.phase === 'saving'}
            text={text}
            onChange={model.change}
          />
        </>
      ) : null}
    </Modal>
  );
}
