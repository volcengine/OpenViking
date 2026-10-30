import type { CompileSkill } from '#/features/workbench/compile/lib/api'
import { skillGuide, skillScope } from '#/features/workbench/compile/lib/skills'
import { Combobox } from '@base-ui/react/combobox'
import { Check, ChevronsUpDown, Search } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

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
  const [highlighted, setHighlighted] = useState<CompileSkill>()
  const [query, setQuery] = useState('')
  const selected =
    skills.find((skill) => skill.uri === value) ??
    (value ? { uri: value, name: value, description: '' } : null)
  const items =
    selected && !skills.some((skill) => skill.uri === value)
      ? [selected, ...skills]
      : skills
  const groups = [
    {
      label: t('builtinSkills'),
      items: items.filter((s) => skillScope(s.uri) === 'builtinSkills'),
    },
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
          !s.uri.startsWith('viking://agent/skills/') &&
          skillScope(s.uri) !== 'builtinSkills',
      ),
    },
  ].filter((group) => group.items.length)
  function matches(skill: CompileSkill, search: string) {
    const guide = skillGuide(skill, t)
    return `${guide?.name || ''} ${guide?.description || ''} ${skill.name} ${skill.description} ${skill.uri}`
      .toLowerCase()
      .includes(search.trim().toLowerCase())
  }
  const matchingItems = items.filter((skill) => matches(skill, query))
  const preview =
    matchingItems.find((skill) => skill.uri === highlighted?.uri) ??
    matchingItems.find((skill) => skill.uri === selected?.uri) ??
    matchingItems.at(0)
  const previewGuide = preview ? skillGuide(preview, t) : null
  return (
    <Combobox.Root
      items={groups}
      disabled={disabled}
      value={selected}
      onOpenChange={() => {
        setHighlighted(undefined)
        setQuery('')
      }}
      onInputValueChange={setQuery}
      onItemHighlighted={(skill) => {
        if (skill) setHighlighted(skill)
      }}
      onValueChange={(skill) => onChange(skill?.uri ?? '')}
      itemToStringLabel={(skill) => skillGuide(skill, t)?.name || skill.name}
      itemToStringValue={(skill) => skill.uri}
      isItemEqualToValue={(a, b) => a.uri === b.uri}
      filter={matches}
    >
      <Combobox.Trigger
        id="compile-skill"
        aria-invalid={invalid}
        aria-describedby="error-skill"
        className="flex h-10 w-full items-center justify-between gap-2 rounded-md border bg-background px-3 text-left text-sm focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50"
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate">
            {selected
              ? skillGuide(selected, t)?.name || selected.name
              : t(loading ? 'loading' : 'chooseSkill')}
          </span>
          {selected && (
            <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              {t(skillScope(selected.uri))}
            </span>
          )}
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" />
      </Combobox.Trigger>
      <Combobox.Portal>
        <Combobox.Positioner sideOffset={6} align="start" className="z-50">
          <Combobox.Popup className="flex max-h-[min(36rem,var(--available-height))] w-[40rem] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-lg bg-popover text-popover-foreground shadow-lg ring-1 ring-foreground/10">
            <div className="flex shrink-0 items-center gap-2 border-b px-3">
              <Search
                aria-hidden
                className="size-4 shrink-0 text-muted-foreground"
              />
              <Combobox.Input
                aria-label={t('skillSearch')}
                placeholder={t('skillSearch')}
                className="h-10 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
            </div>
            <Combobox.Empty className="p-3 text-sm text-muted-foreground empty:hidden">
              {t('noMatchingSkills')}
            </Combobox.Empty>
            <div className="grid min-h-0 overflow-y-auto sm:grid-cols-2 sm:overflow-hidden">
              <Combobox.List className="max-h-64 overflow-y-auto overscroll-contain p-1 sm:max-h-80">
                {(group: { label: string; items: CompileSkill[] }) => (
                  <Combobox.Group key={group.label} items={group.items}>
                    <Combobox.GroupLabel className="px-2 pt-2 pb-1 text-xs font-medium text-muted-foreground">
                      {group.label}
                    </Combobox.GroupLabel>
                    <Combobox.Collection>
                      {(skill: CompileSkill) => {
                        const guide = skillGuide(skill, t)
                        return (
                          <Combobox.Item
                            key={skill.uri}
                            value={skill}
                            className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-2.5 text-sm data-highlighted:bg-accent data-highlighted:text-accent-foreground"
                          >
                            <div className="min-w-0 flex-1">
                              <div className="truncate font-medium">
                                {guide?.name || skill.name}
                              </div>
                              <div className="mt-0.5 truncate text-xs text-muted-foreground">
                                {guide?.description ||
                                  skill.description ||
                                  t('skillDescriptionUnavailable')}
                              </div>
                            </div>
                            <span className="size-4 shrink-0">
                              <Combobox.ItemIndicator>
                                <Check className="size-4" />
                              </Combobox.ItemIndicator>
                            </span>
                          </Combobox.Item>
                        )
                      }}
                    </Combobox.Collection>
                  </Combobox.Group>
                )}
              </Combobox.List>
              {preview && (
                <section
                  aria-label={t('skillPreview')}
                  className="max-h-64 overflow-y-auto overscroll-contain border-t bg-muted/30 p-4 text-xs leading-relaxed sm:max-h-80 sm:border-t-0 sm:border-l"
                >
                  <header className="space-y-2">
                    <h3 className="break-words text-lg font-semibold leading-snug">
                      {previewGuide?.name || preview.name}
                    </h3>
                    <p className="break-words text-muted-foreground">
                      {previewGuide?.description ||
                        preview.description ||
                        t('skillDescriptionUnavailable')}
                    </p>
                    {previewGuide?.specialized && (
                      <p className="text-muted-foreground">
                        {t('specializedSkill')}
                      </p>
                    )}
                  </header>
                  {previewGuide && (
                    <dl className="mt-4 space-y-4 border-t pt-4">
                      <div className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-3">
                        <dt className="text-muted-foreground">
                          {t('suitableInputs')}
                        </dt>
                        <dd>{previewGuide.inputs}</dd>
                      </div>
                      <div className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-3">
                        <dt className="text-muted-foreground">
                          {t('expectedOutputs')}
                        </dt>
                        <dd>{previewGuide.outputs}</dd>
                      </div>
                      <div className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-3">
                        <dt className="text-muted-foreground">
                          {t('usageLimits')}
                        </dt>
                        <dd>{previewGuide.limits}</dd>
                      </div>
                    </dl>
                  )}
                </section>
              )}
            </div>
          </Combobox.Popup>
        </Combobox.Positioner>
      </Combobox.Portal>
    </Combobox.Root>
  )
}
