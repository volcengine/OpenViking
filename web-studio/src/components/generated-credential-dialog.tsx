import * as React from 'react'
import {
  CheckIcon,
  CopyIcon,
  KeyRoundIcon,
  LoaderCircleIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { useAppConnection } from '#/hooks/use-app-connection'
import { copyTextToClipboard } from '#/lib/clipboard'

export function GeneratedCredentialDialog() {
  const { t } = useTranslation('settings')
  const {
    clearGeneratedCredential,
    connection,
    generatedCredential,
    switchIdentity,
  } = useAppConnection()
  const [isSwitching, setIsSwitching] = React.useState(false)

  if (!generatedCredential?.apiKey) {
    return null
  }

  const accountId = generatedCredential.accountId || connection.accountId
  const userId = generatedCredential.userId || connection.userId
  const isCurrentIdentity =
    accountId === connection.accountId &&
    userId === connection.userId &&
    generatedCredential.apiKey === connection.apiKey

  async function copyKey(): Promise<void> {
    try {
      await copyTextToClipboard(generatedCredential.apiKey)
      toast.success(t('toast.copied'))
    } catch {
      toast.error(t('toast.copyFailed'))
    }
  }

  async function useIdentity(): Promise<void> {
    setIsSwitching(true)
    try {
      await switchIdentity({
        accountId,
        allowLegacyIdentityFallback: true,
        apiKey: generatedCredential.apiKey,
        userId,
      })
      toast.success(t('toast.dataKeySelected'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setIsSwitching(false)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !isSwitching) {
          clearGeneratedCredential()
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('keyResult.title')}</DialogTitle>
          <DialogDescription>
            {t('keyResult.description', { account: accountId, user: userId })}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 py-4">
          <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
            {accountId} / {userId}
          </div>
          <code className="break-all rounded-md border bg-background p-3 font-mono text-sm">
            {generatedCredential.apiKey}
          </code>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => void copyKey()}
          >
            <CopyIcon />
            {t('actions.copy')}
          </Button>
          {isCurrentIdentity ? (
            <Badge variant="secondary" className="h-9 gap-1.5 px-3 font-normal">
              <CheckIcon />
              {t('actions.currentIdentity')}
            </Badge>
          ) : (
            <Button
              type="button"
              disabled={isSwitching}
              onClick={() => void useIdentity()}
            >
              {isSwitching ? (
                <LoaderCircleIcon className="animate-spin" />
              ) : (
                <KeyRoundIcon />
              )}
              {t('actions.switchIdentity')}
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            disabled={isSwitching}
            onClick={clearGeneratedCredential}
          >
            {t('keyResult.dismiss')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
