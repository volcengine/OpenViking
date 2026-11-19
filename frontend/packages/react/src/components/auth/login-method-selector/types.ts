import type { ReactNode } from 'react';
export interface LoginMethodSelectorProps {
  methods: readonly {
    id: string;
    label: string;
    icon?: ReactNode;
    disabled?: boolean;
  }[];
  disabled?: boolean;
  className?: string;
  title?: ReactNode;
  description?: ReactNode;
  onSelect?: (id: string) => void;
}
