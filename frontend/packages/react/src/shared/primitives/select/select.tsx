import { useCallback, useId } from 'react';
import { useLocale } from '../../locale-context.js';
import { DropdownSurface } from '../dropdown/dropdown-surface.js';
import { SelectPanel } from './select-panel.js';
import { SelectTrigger } from './select-trigger.js';
import { useSelect } from './use-select.js';
import { useSelectKeyboard } from './use-select-keyboard.js';
import { useSelectedOptions } from './use-selected-options.js';
import type { SelectProps, SelectTriggerState, SelectValue } from './types.js';
import en from './locales/en-US.js';
import zh from './locales/zh-CN.js';

/** Internal selection field. Values use arrays in both single and multiple modes. */
export function Select<V extends SelectValue = SelectValue>(
  props: SelectProps<V>,
) {
  const {
    children,
    value: _value,
    defaultValue: _defaultValue,
    multiple = false,
    variant = 'input',
    size = 'default',
    placeholder,
    disabled = false,
    status,
    maxTagCount = 2,
    searchable,
    searchPlaceholder,
    filterOption,
    loading,
    loadingContent,
    searching,
    searchingContent,
    emptyContent,
    onSearch,
    open: _open,
    onOpenChange: _onOpenChange,
    onChange: _onChange,
    renderValue,
    renderTrigger,
    popupFooter,
    popupClassName,
    listboxClassName,
    popupStyle,
    placement = 'bottom',
    alignment = 'start',
    name,
    locale: localeProp,
    onClick,
    onKeyDown,
    ...attributes
  } = props;
  const locale = useLocale(localeProp);
  const text = locale === 'zh-CN' ? zh : en;
  const state = useSelect(props);
  const keyboard = useSelectKeyboard(state);
  const generatedId = useId();
  const id = props.id ?? `${generatedId}-trigger`;
  const listboxId = `${generatedId}-listbox`;
  const selectedOptions = useSelectedOptions(children, state.values);
  const triggerRef = useCallback(
    (node: HTMLElement | null) => {
      state.trigger.current = node;
      state.anchor.current = node;
    },
    [state.trigger, state.anchor],
  );
  const frameRef = useCallback(
    (node: HTMLElement | null) => {
      state.anchor.current = node;
    },
    [state.anchor],
  );
  const content = renderValue ? (
    renderValue(selectedOptions)
  ) : selectedOptions.length ? (
    selectedOptions.map((option, index) => (
      <span key={option.value}>
        {index ? ', ' : ''}
        {option.label}
      </span>
    ))
  ) : (
    <span className="ov-select__placeholder">
      {placeholder === undefined ? text.placeholder : placeholder}
    </span>
  );
  const triggerState: SelectTriggerState<V> = {
    triggerRef,
    content,
    selectedOptions,
    open: state.open,
    triggerProps: {
      ...attributes,
      id,
      role: multiple ? 'button' : 'combobox',
      disabled,
      tabIndex: disabled ? -1 : (attributes.tabIndex ?? 0),
      'aria-haspopup': 'listbox',
      'aria-expanded': state.open,
      'aria-controls': state.open ? listboxId : undefined,
      'aria-invalid': status === 'error' ? true : attributes['aria-invalid'],
      onClick: (event) => {
        onClick?.(event);
        if (!event.defaultPrevented) state.setOpen(!state.open);
      },
      onKeyDown: (event) => {
        onKeyDown?.(event);
        keyboard.triggerKey(event);
      },
    },
  };
  return (
    <>
      {renderTrigger ? (
        renderTrigger(triggerState)
      ) : (
        <SelectTrigger
          state={triggerState}
          frameRef={frameRef}
          size={size}
          variant={variant}
          multiple={multiple}
          disabled={disabled}
          renderValue={renderValue}
          maxTagCount={maxTagCount}
          locale={locale}
          onRemove={(value) => {
            state.update(state.values.filter((item) => item !== value));
            state.trigger.current?.focus();
          }}
        />
      )}
      {name
        ? state.values.map((value) => (
            <input
              key={value}
              type="hidden"
              name={name}
              value={value}
              disabled={disabled}
            />
          ))
        : null}
      {state.open ? (
        <DropdownSurface
          anchor={state.anchor}
          surfaceRef={state.panel}
          placement={placement}
          alignment={alignment}
          onReady={keyboard.onReady}
          className={['ov-select__popover', popupClassName]
            .filter(Boolean)
            .join(' ')}
          style={popupStyle}
          matchAnchorWidth={variant === 'input' && !renderTrigger}
        >
          <SelectPanel
            state={state}
            keyboard={keyboard}
            id={listboxId}
            triggerId={id}
            multiple={multiple}
            searchable={searchable}
            searchPlaceholder={searchPlaceholder}
            filterOption={filterOption}
            loading={loading}
            loadingContent={loadingContent}
            searching={searching}
            searchingContent={searchingContent}
            emptyContent={emptyContent}
            onSearch={onSearch}
            listboxClassName={listboxClassName}
            popupFooter={popupFooter}
            locale={locale}
          >
            {children}
          </SelectPanel>
        </DropdownSurface>
      ) : null}
    </>
  );
}
