import type {
  ButtonHTMLAttributes,
  CSSProperties,
  HTMLAttributes,
  ReactElement,
  ReactNode,
  RefCallback,
} from 'react';
import type { Locale } from '../../locale.js';
import type { PopoverPlacement } from '../popover/types.js';

export type SelectValue = string | number;

export interface SelectSelectedOption<V extends SelectValue = SelectValue> {
  value: V;
  label: ReactNode;
  disabled: boolean;
}

export interface SelectTriggerState<V extends SelectValue = SelectValue> {
  triggerRef: RefCallback<HTMLElement>;
  triggerProps: HTMLAttributes<HTMLElement> & { disabled: boolean };
  selectedOptions: SelectSelectedOption<V>[];
  content: ReactNode;
  open: boolean;
}

export interface SelectProps<V extends SelectValue = SelectValue> extends Omit<
  HTMLAttributes<HTMLElement>,
  'children' | 'onChange' | 'defaultValue' | 'onSelect'
> {
  children: ReactNode;
  value?: readonly V[];
  defaultValue?: readonly V[];
  multiple?: boolean;
  variant?: 'input' | 'simple';
  size?: 'mini' | 'small' | 'default' | 'large';
  placeholder?: ReactNode;
  disabled?: boolean;
  status?: 'error';
  maxTagCount?: number;
  searchable?: boolean;
  searchPlaceholder?: string;
  filterOption?: boolean;
  loading?: boolean;
  loadingContent?: ReactNode;
  searching?: boolean;
  searchingContent?: ReactNode;
  emptyContent?: ReactNode;
  open?: boolean;
  name?: string;
  placement?: PopoverPlacement;
  alignment?: 'start' | 'center' | 'end';
  popupClassName?: string;
  listboxClassName?: string;
  popupStyle?: CSSProperties;
  popupFooter?: ReactNode;
  locale?: Locale;
  onChange?: (values: V[]) => void;
  onOpenChange?: (open: boolean) => void;
  onSearch?: (keyword: string) => void;
  renderValue?: (selectedOptions: SelectSelectedOption<V>[]) => ReactNode;
  renderTrigger?: (state: SelectTriggerState<V>) => ReactElement;
}

export interface SelectOptionProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'value' | 'role' | 'type' | 'tabIndex'
> {
  value: SelectValue;
  label?: ReactNode;
}
