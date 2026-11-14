import { LinkIcon, PuzzleIcon, RefreshCwIcon, UploadIcon } from 'lucide-react'
import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { VISIBLE_REMOTE_RESOURCE_DESCRIPTORS } from '#/features/workbench/resources/components/remote-resource-descriptors'
import type { RemoteResourceTypeSelection } from '#/features/workbench/resources/lib/resource-source'
import { getRemoteResourceCapabilities } from '#/features/workbench/resources/lib/resource-source-strategy'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '#/shared/ui/dropdown-menu'

export type ResourceImportEntry =
  | { mode: 'upload' }
  | { mode: 'remote'; source: RemoteResourceTypeSelection; sync?: boolean }

export function ResourceImportMenu({
  trigger,
  onImport,
  onImportSkill,
}: {
  trigger: ReactElement
  onImport: (entry: ResourceImportEntry) => void
  onImportSkill: () => void
}) {
  const { t } = useTranslation(['filesystem', 'addResource'])

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger render={trigger} />
      <DropdownMenuContent
        align="end"
        className="w-44 rounded-lg p-1.5 [&_[role=menuitem]]:min-h-9"
      >
        <DropdownMenuGroup>
          <DropdownMenuLabel>{t('importMenu.resources')}</DropdownMenuLabel>
          <DropdownMenuItem onClick={() => onImport({ mode: 'upload' })}>
            <UploadIcon />
            {t('importMenu.localUpload')}
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => onImport({ mode: 'remote', source: 'webPage' })}
          >
            <LinkIcon />
            {t('importMenu.webLink')}
          </DropdownMenuItem>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <RefreshCwIcon />
              {t('importMenu.sync')}
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="min-w-36 rounded-lg p-1.5">
              {VISIBLE_REMOTE_RESOURCE_DESCRIPTORS.filter(
                ({ type }) => getRemoteResourceCapabilities(type).watch,
              ).map(({ type, icon: Icon }) => (
                <DropdownMenuItem
                  key={type}
                  onClick={() =>
                    onImport({ mode: 'remote', source: type, sync: true })
                  }
                >
                  <Icon />
                  {t(`addResource:sourcePicker.${type}`)}
                </DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={onImportSkill}>
          <PuzzleIcon />
          {t('importMenu.skill')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
