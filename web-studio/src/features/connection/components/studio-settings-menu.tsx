import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import type { StudioMode } from '#/features/connection/lib/studio-mode'
import {
  readPreferredMode,
  savePreferredMode,
} from '#/features/connection/lib/studio-mode'
import { cn } from '#/shared/lib/utils'
import { Link } from '@tanstack/react-router'
import { Popover, PopoverContent, PopoverTrigger } from '#/shared/ui/popover'
import {
  CheckIcon,
  CableIcon,
  ChevronDownIcon,
  GithubIcon,
  LanguagesIcon,
  MoonIcon,
  PanelsTopLeftIcon,
  PlusIcon,
  ServerIcon,
  SettingsIcon,
  ShieldCheckIcon,
  SunIcon,
} from 'lucide-react'
import { useTheme } from 'next-themes'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

const ENTRY_MODES = ['workbench', 'management'] as const
const LANGUAGE_OPTIONS = [
  { value: 'zh-CN', label: '中文' },
  { value: 'en', label: 'English' },
] as const

const MENU_ITEM =
  'flex min-h-10 w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring'

export function StudioSettingsMenu({
  canManage,
  managementMode,
}: {
  canManage: boolean
  managementMode: boolean
}) {
  const { t, i18n } = useTranslation('appShell')
  const { setTheme, resolvedTheme } = useTheme()
  const {
    connection,
    serverMode,
    connectionRole,
    profiles = [],
    activeProfileId,
    activateProfile,
  } = useAppConnection()
  const settingsPath = managementMode ? '/admin/settings' : '/settings'
  const workbenchKey = profiles.find(
    (profile) => profile.id === activeProfileId,
  )?.workbenchIdentity?.apiKey
  const userName = connection.userId.trim()
  const showUser =
    !managementMode &&
    Boolean(userName) &&
    (Boolean(connection.apiKey) ||
      serverMode === 'trusted' ||
      serverMode === 'dev')
  const managementIdentity =
    !showUser && (connectionRole === 'root' || connectionRole === 'admin')
  const identityName = showUser
    ? userName
    : managementIdentity
      ? t(
          connectionRole === 'root'
            ? 'studioSettings.root'
            : 'studioSettings.accountAdmin',
        )
      : t('studioSettings.settings')
  const initials = Array.from(userName).slice(0, 2).join('').toLocaleUpperCase()
  const [open, setOpen] = React.useState(false)
  const [preferredMode, setPreferredMode] = React.useState(() =>
    readPreferredMode(connection, activeProfileId),
  )
  const language = (i18n.resolvedLanguage ?? i18n.language).startsWith('zh')
    ? 'zh-CN'
    : 'en'
  const effectiveDefault =
    preferredMode !== 'management' &&
    (connection.apiKey ||
      workbenchKey ||
      serverMode === 'trusted' ||
      serverMode === 'dev')
      ? 'workbench'
      : 'management'
  function chooseDefault(mode: StudioMode) {
    savePreferredMode(connection, mode, activeProfileId)
    setPreferredMode(mode)
  }
  const segmentClass = (active: boolean) =>
    cn(
      'flex min-h-7 items-center justify-center gap-1 rounded-md px-2 text-xs outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40',
      active
        ? 'bg-background text-foreground shadow-xs ring-1 ring-border/60'
        : 'text-muted-foreground hover:text-foreground',
    )

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={t('studioSettings.open')}
        title={identityName}
        className="flex h-9 shrink-0 items-center gap-2 rounded-lg px-2 text-sm outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
      >
        {showUser ? (
          <span
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-semibold ring-1 ring-border/70"
          >
            {initials}
          </span>
        ) : managementIdentity ? (
          <ShieldCheckIcon
            aria-hidden="true"
            className="size-4 shrink-0 text-muted-foreground"
          />
        ) : (
          <SettingsIcon
            aria-hidden="true"
            className="size-4 shrink-0 text-muted-foreground"
          />
        )}
        <span
          className={cn('max-w-32 truncate', showUser && 'hidden sm:inline')}
        >
          {identityName}
        </span>
        <ChevronDownIcon className="hidden size-3 sm:block" />
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={10}
        className="max-h-[calc(100svh-5rem)] w-[min(23rem,calc(100vw-1.5rem))] gap-0 overflow-y-auto rounded-xl p-2 shadow-xl"
      >
        <div
          role="group"
          aria-label={t('studioSettings.connections')}
          className="border-b border-border/60 pb-2"
        >
          <p className="px-2.5 py-2 text-xs font-medium text-muted-foreground">
            {t('studioSettings.connections')}
          </p>
          <div className="max-h-52 overflow-y-auto">
            {profiles.map((profile) => {
              const active = profile.id === activeProfileId
              return (
                <button
                  key={profile.id}
                  type="button"
                  aria-pressed={active}
                  disabled={active}
                  className={cn(MENU_ITEM, active && 'bg-accent')}
                  onClick={() => {
                    setOpen(false)
                    activateProfile(profile.id)
                  }}
                >
                  <ServerIcon className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">
                      {profile.name}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {profile.connection.baseUrl}
                    </span>
                  </span>
                  {active ? (
                    <>
                      <CheckIcon
                        aria-hidden="true"
                        className="size-4 shrink-0"
                      />
                      <span className="sr-only">
                        {t('studio:profiles.active')}
                      </span>
                    </>
                  ) : null}
                </button>
              )
            })}
          </div>
          <Link
            to={managementMode ? '/admin/connections/new' : '/connections/new'}
            onClick={() => setOpen(false)}
            className={MENU_ITEM}
          >
            <PlusIcon className="size-4 text-muted-foreground" />
            {t('studioSettings.addConnection')}
          </Link>
          <Link
            to={settingsPath}
            onClick={() => setOpen(false)}
            className={MENU_ITEM}
          >
            <CableIcon className="size-4 text-muted-foreground" />
            {t('studioSettings.manageConnections')}
          </Link>
        </div>
        {canManage ? (
          <div className="flex items-center justify-between gap-3 px-2.5 py-3">
            <span className="flex items-center gap-2.5 text-xs">
              <PanelsTopLeftIcon className="size-4 text-muted-foreground" />
              {t('studioSettings.defaultEntry')}
            </span>
            <div
              role="group"
              aria-label={t('studioSettings.defaultEntry')}
              className="flex rounded-lg bg-muted p-0.5"
            >
              {ENTRY_MODES.map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={effectiveDefault === mode}
                  disabled={
                    mode === 'workbench' &&
                    !connection.apiKey &&
                    !workbenchKey &&
                    serverMode !== 'trusted' &&
                    serverMode !== 'dev'
                  }
                  className={segmentClass(effectiveDefault === mode)}
                  onClick={() => chooseDefault(mode)}
                >
                  {effectiveDefault === mode ? (
                    <CheckIcon className="size-3" />
                  ) : null}
                  {t(`studioSettings.${mode}`)}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <div
          className={cn(
            'px-2.5 py-2',
            canManage && 'border-t border-border/60',
          )}
        >
          <div className="flex min-h-11 items-center justify-between gap-3">
            <span className="flex items-center gap-2.5 text-xs">
              <LanguagesIcon className="size-4 text-muted-foreground" />
              {t('studioSettings.language')}
            </span>
            <div
              role="group"
              aria-label={t('studioSettings.language')}
              className="flex rounded-lg bg-muted p-0.5"
            >
              {LANGUAGE_OPTIONS.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  aria-pressed={language === item.value}
                  className={segmentClass(language === item.value)}
                  onClick={() => {
                    void i18n.changeLanguage(item.value)
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex min-h-11 items-center justify-between gap-3">
            <span className="flex items-center gap-2.5 text-xs">
              <SunIcon className="size-4 text-muted-foreground" />
              {t('studioSettings.theme')}
            </span>
            <div
              role="group"
              aria-label={t('studioSettings.theme')}
              className="flex rounded-lg bg-muted p-0.5"
            >
              <button
                type="button"
                aria-pressed={resolvedTheme === 'light'}
                className={segmentClass(resolvedTheme === 'light')}
                onClick={() => setTheme('light')}
              >
                <SunIcon className="size-3" />
                {t('studioSettings.light')}
              </button>
              <button
                type="button"
                aria-pressed={resolvedTheme === 'dark'}
                className={segmentClass(resolvedTheme === 'dark')}
                onClick={() => setTheme('dark')}
              >
                <MoonIcon className="size-3" />
                {t('studioSettings.dark')}
              </button>
            </div>
          </div>
        </div>
        <a
          href="https://github.com/volcengine/OpenViking"
          target="_blank"
          rel="noreferrer"
          className={cn(
            MENU_ITEM,
            'border-t border-border/60 text-muted-foreground',
          )}
        >
          <GithubIcon className="size-4" />
          {t('footer.github')}
        </a>
      </PopoverContent>
    </Popover>
  )
}
