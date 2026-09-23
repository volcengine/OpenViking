import { Combobox } from '@base-ui/react/combobox'
import { Check, ChevronsUpDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { CompileSkill } from '../-lib/api'

export function SkillPicker({
  value,
  skills,
  onChange,
  invalid,
  disabled,
  loading,
}: {
  invalid?: boolean
  disabled?: boolean
  loading?: boolean
  value: string
  skills: CompileSkill[]
  onChange: (uri: string) => void
}) {
  const { t } = useTranslation('compile')
  const selected =
    skills.find((skill) => skill.uri === value) ??
    (value ? { uri: value, name: value, description: '' } : null)
  const items =
    selected && !skills.some((skill) => skill.uri === value)
      ? [selected, ...skills]
      : skills
  const groups = [
    {
      label: t('mySkills'),
      items: items.filter((s) => s.uri.startsWith('viking://user/')),
    },
    {
      label: t('sharedSkills'),
      items: items.filter((s) => s.uri.startsWith('viking://agent/skills/')),
    },
    {
      label: t('otherSkills'),
      items: items.filter(
        (s) =>
          !s.uri.startsWith('viking://user/') &&
          !s.uri.startsWith('viking://agent/skills/'),
      ),
    },
  ].filter((group) => group.items.length)
  return (
    <Combobox.Root
      items={groups}
      disabled={disabled}
      value={selected}
      onValueChange={(skill) => onChange(skill?.uri ?? '')}
      itemToStringLabel={(skill) => skill.name}
      itemToStringValue={(skill) => skill.uri}
      isItemEqualToValue={(a, b) => a.uri === b.uri}
      filter={(skill, query) =>
        `${skill.name} ${skill.description} ${skill.uri}`
          .toLowerCase()
          .includes(query.trim().toLowerCase())
      }
    >
      <Combobox.Trigger
        id="compile-skill"
        aria-invalid={invalid}
        aria-describedby="error-skill"
        className="flex h-10 w-full items-center justify-between gap-2 rounded-md border bg-background px-3 text-left text-sm focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50"
      >
        <span className="truncate">
          {selected
            ? `${selected.name} · ${selected.uri.startsWith('viking://user/') ? t('mySkills') : t('sharedSkills')}`
            : t(loading ? 'loading' : 'chooseSkill')}
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" />
      </Combobox.Trigger>
      <Combobox.Portal>
        <Combobox.Positioner sideOffset={4} align="start" className="z-50">
          <Combobox.Popup className="w-[var(--anchor-width)] max-w-[calc(100vw-2rem)] rounded-md border bg-popover p-1 text-popover-foreground shadow-md">
            <Combobox.Input
              aria-label={t('skillSearch')}
              placeholder={t('skillSearch')}
              className="h-10 w-full border-b bg-transparent px-3 text-sm outline-none"
            />
            <Combobox.Empty className="p-3 text-sm text-muted-foreground">
              {t('noMatchingSkills')}
            </Combobox.Empty>
            <Combobox.List className="max-h-64 overflow-y-auto overscroll-contain">
              {(group: { label: string; items: CompileSkill[] }) => (
                <Combobox.Group key={group.label} items={group.items}>
                  <Combobox.GroupLabel className="px-3 pt-3 pb-1 text-xs font-medium text-muted-foreground">
                    {group.label}
                  </Combobox.GroupLabel>
                  <Combobox.Collection>
                    {(skill: CompileSkill) => (
                      <Combobox.Item
                        key={skill.uri}
                        value={skill}
                        className="flex cursor-pointer items-center gap-2 rounded px-3 py-2 text-sm data-highlighted:bg-accent data-highlighted:text-accent-foreground"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="truncate">{skill.name}</div>
                          <div className="break-all text-xs text-muted-foreground">
                            {skill.uri}
                          </div>
                        </div>
                        <Combobox.ItemIndicator>
                          <Check className="size-4" />
                        </Combobox.ItemIndicator>
                      </Combobox.Item>
                    )}
                  </Combobox.Collection>
                </Combobox.Group>
              )}
            </Combobox.List>
          </Combobox.Popup>
        </Combobox.Positioner>
      </Combobox.Portal>
    </Combobox.Root>
  )
}
