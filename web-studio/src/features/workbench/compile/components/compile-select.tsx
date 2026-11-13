import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/shared/ui/select'

export function CompileSelect({
  label,
  value,
  options,
  onValueChange,
  className,
}: {
  label: string
  value: string
  options: { value: string; label: string }[]
  onValueChange: (value: string) => void
  className?: string
}) {
  return (
    <Select
      items={options}
      value={value}
      onValueChange={(next) => {
        if (next !== null) onValueChange(next)
      }}
    >
      <SelectTrigger aria-label={label} className={className}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent
        align="start"
        className="max-h-[min(16rem,var(--available-height))]"
      >
        <SelectGroup>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
