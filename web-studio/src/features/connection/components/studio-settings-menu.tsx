import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { validateFilesystemSearch } from '#/features/workbench/filesystem/lib/types'
import { cn } from '#/shared/lib/utils'
import { Link } from '@tanstack/react-router'
import { Popover, PopoverContent, PopoverTrigger } from '#/shared/ui/popover'
import {
  CheckIcon,
  CableIcon,
  ChevronRightIcon,
  KeyRoundIcon,
  LockKeyholeIcon,
  LanguagesIcon,
  MoonIcon,
  PieChartIcon,
  SettingsIcon,
  ShieldCheckIcon,
  SunMoonIcon,
  SunIcon,
  UserRoundPlusIcon,
} from 'lucide-react'
import { useTheme } from 'next-themes'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

const LANGUAGE_OPTIONS = [
  { value: 'zh-CN', label: '中文' },
  { value: 'en', label: 'English' },
] as const

const MENU_ITEM =
  'flex min-h-11 w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring'
const AVATAR =
  'relative flex shrink-0 items-center justify-center rounded-full font-medium text-white'
const MENU_SURFACE =
  'gap-1 rounded-xl bg-popover p-1.5 shadow-[0_2px_6px_#0b0b0f0f,0_8px_24px_#0b0b0f1a] ring-border/60'

function SettingsAvatar({
  name,
  usesApiKey,
  className,
}: {
  name: string
  usesApiKey: boolean
  className: string
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        AVATAR,
        className,
        usesApiKey ? 'bg-[#7672ff]' : 'bg-[#129d86]',
      )}
    >
      {Array.from(name).slice(0, 2).join('').toLocaleUpperCase()}
      {usesApiKey ? (
        <span className="absolute -right-0.5 -bottom-0.5 flex size-4 items-center justify-center rounded-full border border-border bg-popover text-popover-foreground">
          <KeyRoundIcon className="size-2.5" />
        </span>
      ) : null}
    </span>
  )
}

export function StudioSettingsMenu({
  managementMode,
}: {
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
  const activeProfile = profiles.find(
    (profile) => profile.id === activeProfileId,
  )
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
  const usesApiKey =
    serverMode !== 'trusted' &&
    serverMode !== 'dev' &&
    Boolean(showUser ? connection.apiKey : connection.adminApiKey)
  const [open, setOpen] = React.useState(false)
  const language = (i18n.resolvedLanguage ?? i18n.language).startsWith('zh')
    ? 'zh-CN'
    : 'en'
  const segmentClass = (active: boolean) =>
    cn(
      'flex min-h-7 items-center justify-center gap-1 rounded-md px-2.5 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40',
      active
        ? 'bg-background text-foreground shadow-xs'
        : 'text-muted-foreground hover:text-foreground',
    )

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={t('studioSettings.open')}
        title={identityName}
        className={cn(
          'flex shrink-0 items-center justify-center rounded-full outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring',
          managementMode ? 'size-8' : 'size-7',
        )}
      >
        {showUser || managementMode ? (
          <SettingsAvatar
            name={identityName}
            usesApiKey={managementMode && usesApiKey}
            className={
              managementMode ? 'size-6 text-xs' : 'size-7 text-xs lowercase'
            }
          />
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
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={6}
        className={cn(
          MENU_SURFACE,
          'max-h-[calc(100svh-5rem)] w-[min(330px,calc(100vw-1.5rem))] overflow-y-auto',
        )}
      >
        <div className="flex min-w-0 items-center gap-2.5 px-3 pt-3 pb-4">
          <SettingsAvatar
            name={identityName}
            usesApiKey={usesApiKey}
            className="size-8 text-sm"
          />
          <div className="min-w-0">
            <p className="truncate text-base font-medium" title={identityName}>
              {identityName}
            </p>
            {usesApiKey ? (
              <p className="text-[13px] text-muted-foreground">
                {t('studioSettings.apiKeySignIn')}
              </p>
            ) : activeProfile && activeProfile.name !== identityName ? (
              <p className="truncate text-xs text-muted-foreground">
                {activeProfile.name}
              </p>
            ) : null}
          </div>
        </div>
        {!managementMode ? (
          <div className="border-b border-border/70 pb-1">
            <Link
              to="/usage"
              search={(previous) => {
                const { file, uri, panel, session } =
                  validateFilesystemSearch(previous)
                return { file, uri, panel, session }
              }}
              onClick={() => setOpen(false)}
              className={MENU_ITEM}
            >
              <PieChartIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="flex-1">{t('home:myUsage')}</span>
              <ChevronRightIcon className="size-3 text-muted-foreground" />
            </Link>
          </div>
        ) : null}
        <div className="px-3 pb-1">
          <div className="flex min-h-11 items-center justify-between gap-3">
            <span className="flex items-center gap-3 text-sm">
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
            <span className="flex items-center gap-3 text-sm">
              <SunMoonIcon className="size-4 text-muted-foreground" />
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
        <div className="mt-1 border-t border-border/70 pt-1">
          <Popover>
            <PopoverTrigger
              className={cn(MENU_ITEM, 'aria-expanded:bg-accent')}
            >
              <LockKeyholeIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="flex-1">
                {t('studioSettings.switchAccount')}
              </span>
              <ChevronRightIcon className="size-3 text-muted-foreground" />
            </PopoverTrigger>
            <PopoverContent
              side="left"
              align="start"
              sideOffset={12}
              className={cn(
                MENU_SURFACE,
                'max-h-[calc(100svh-2rem)] w-[min(320px,calc(100vw-1.5rem))] overflow-y-auto',
              )}
            >
              <div role="group" aria-label={t('studioSettings.connections')}>
                <div className="max-h-52 overflow-y-auto">
                  {profiles.map((profile) => {
                    const active = profile.id === activeProfileId
                    return (
                      <button
                        key={profile.id}
                        type="button"
                        aria-pressed={active}
                        disabled={active}
                        className={MENU_ITEM}
                        onClick={() => {
                          setOpen(false)
                          activateProfile(profile.id)
                        }}
                      >
                        <SettingsAvatar
                          name={profile.name}
                          usesApiKey={Boolean(
                            profile.connection.apiKey ||
                            profile.connection.adminApiKey,
                          )}
                          className="size-8 text-sm"
                        />
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
                              className="size-4 shrink-0 text-primary"
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
              </div>
              <Link
                to={
                  managementMode ? '/admin/connections/new' : '/connections/new'
                }
                onClick={() => setOpen(false)}
                className={cn(
                  MENU_ITEM,
                  'border-t border-border/70 text-primary',
                )}
              >
                <UserRoundPlusIcon className="size-4" />
                {t('studioSettings.addConnection')}
              </Link>
            </PopoverContent>
          </Popover>
          <Link
            to={settingsPath}
            onClick={() => setOpen(false)}
            className={MENU_ITEM}
          >
            <CableIcon className="size-4 text-muted-foreground" />
            {t('studioSettings.manageConnections')}
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  )
}
