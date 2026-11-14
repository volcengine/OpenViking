import type { ReactNode } from 'react'
import { cn } from '#/shared/lib/utils'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '#/shared/ui/select'

export type FilterSelectOption = {
  value: string
  label: string
  description?: string
  disabled?: boolean
  icon?: ReactNode
}

/** Compact selectors share the same listbox, keyboard navigation and focus style. */
export function FilterSelect({
  label,
  value,
  onValueChange,
  options,
  icon,
  placeholder,
  disabled,
  open,
  onOpenChange,
  loading,
  loadingLabel,
  error,
  retryLabel,
  onRetry,
  className,
}: {
  label: string
  value: string
  onValueChange: (value: string) => void
  options: FilterSelectOption[]
  icon?: ReactNode
  placeholder?: string
  disabled?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
  loading?: boolean
  loadingLabel?: string
  error?: boolean
  retryLabel?: string
  onRetry?: () => void
  className?: string
}) {
  const selected = options.find((option) => option.value === value)
  return (
    <Select
      value={value}
      disabled={disabled}
      open={open}
      onOpenChange={onOpenChange}
      onValueChange={(next) => {
        if (next !== null) onValueChange(next)
      }}
    >
      <SelectTrigger
        aria-label={label}
        className={cn('h-9 min-w-44 max-w-full', className)}
      >
        {icon}
        <span className="min-w-0 flex-1 truncate text-left">
          {selected?.label ?? placeholder ?? value}
        </span>
      </SelectTrigger>
      <SelectContent align="start" className="max-h-64 max-w-xs">
        {options.map((option) => (
          <SelectItem
            key={option.value}
            value={option.value}
            disabled={option.disabled}
          >
            {option.icon}
            <span className="min-w-0 flex-1 whitespace-normal">
              <span className="block truncate">{option.label}</span>
              {option.description && (
                <span className="block text-xs leading-4 text-muted-foreground">
                  {option.description}
                </span>
              )}
            </span>
          </SelectItem>
        ))}
        {loading && (
          <p role="status" className="px-2 py-2 text-xs text-muted-foreground">
            {loadingLabel}
          </p>
        )}
        {error && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="w-full rounded-sm px-2 py-1.5 text-left text-sm text-primary outline-none focus-visible:bg-accent"
          >
            {retryLabel}
          </button>
        )}
      </SelectContent>
    </Select>
  )
}
