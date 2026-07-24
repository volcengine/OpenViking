import { Building2Icon, ChevronDownIcon, UserRoundIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '#/components/ui/popover'

type CurrentUserMenuProps = {
  accountId: string
  userId: string
}

export function getUserInitial(userId: string): string {
  const normalizedUserId = userId.trim()
  return normalizedUserId ? normalizedUserId.slice(0, 1).toUpperCase() : '?'
}

export function CurrentUserMenu({ accountId, userId }: CurrentUserMenuProps) {
  const { t } = useTranslation('appShell')
  const accountLabel = accountId || t('header.currentUser.unset')
  const userLabel = userId || t('header.currentUser.unset')

  return (
    <Popover>
      <PopoverTrigger
        aria-label={t('header.currentUser.openMenu', { user: userLabel })}
        className="group flex h-10 max-w-52 items-center gap-2 rounded-2xl border border-border/80 bg-muted/60 p-1 pr-2.5 text-left shadow-xs outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-foreground text-xs font-semibold text-background shadow-sm">
          {getUserInitial(userLabel)}
        </span>
        <span className="hidden min-w-0 flex-1 sm:block">
          <span className="block truncate text-xs font-semibold leading-4 text-foreground">
            {userLabel}
          </span>
          <span className="block truncate text-[10px] leading-3 text-muted-foreground">
            {t('header.currentUser.accountSummary', {
              account: accountLabel,
            })}
          </span>
        </span>
        <ChevronDownIcon className="hidden size-3.5 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180 sm:block" />
      </PopoverTrigger>

      <PopoverContent
        align="end"
        side="bottom"
        sideOffset={8}
        className="w-72 gap-0 overflow-hidden p-0"
      >
        <div className="flex items-center gap-3 border-b bg-muted/35 px-4 py-3.5">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-2xl bg-foreground text-sm font-semibold text-background shadow-sm">
            {getUserInitial(userLabel)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{userLabel}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {t('header.currentUser.signedInAs')}
            </div>
          </div>
        </div>

        <dl className="space-y-1 p-2">
          <div className="flex items-center gap-3 rounded-lg px-2.5 py-2">
            <Building2Icon className="size-4 shrink-0 text-muted-foreground" />
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">
              {t('header.currentUser.account')}
            </dt>
            <dd className="min-w-0 flex-1 truncate text-right text-xs font-medium">
              {accountLabel}
            </dd>
          </div>
          <div className="flex items-center gap-3 rounded-lg px-2.5 py-2">
            <UserRoundIcon className="size-4 shrink-0 text-muted-foreground" />
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">
              {t('header.currentUser.user')}
            </dt>
            <dd className="min-w-0 flex-1 truncate text-right text-xs font-medium">
              {userLabel}
            </dd>
          </div>
        </dl>
      </PopoverContent>
    </Popover>
  )
}
