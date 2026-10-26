import { useMutation } from '@tanstack/react-query'
import { LoaderCircleIcon, SparklesIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'

import { newObjectId, saveProfile } from '../-lib/api'
import { toastGatewayError } from '../-lib/error-toast'
import { PROFILE_DEFAULTS } from '../-lib/profile-schema'
import { useGateway } from '../-lib/use-gateway'

type RecommendedProfileButtonProps = {
  variant?: 'default' | 'outline'
  /** E.g. until the profile list has loaded, so a second click can't duplicate it. */
  disabled?: boolean
}

/** "Create with recommended settings": saves the default profile in one click. */
export function RecommendedProfileButton({
  variant = 'default',
  disabled = false,
}: RecommendedProfileButtonProps) {
  const { t } = useTranslation('gateway')
  const { connection, invalidate, readOnly } = useGateway()
  const create = useMutation({
    mutationFn: () =>
      saveProfile(connection, newObjectId(), {
        ...PROFILE_DEFAULTS,
        name: t('profiles.defaultName'),
      }),
    onSuccess: async (profile) => {
      toast.success(t('profiles.toast.created', { name: profile.name }))
      await invalidate('profiles')
    },
    onError: (error) => toastGatewayError(t, error),
  })
  if (readOnly) return null
  return (
    <Button
      type="button"
      size="sm"
      variant={variant}
      disabled={disabled || create.isPending}
      onClick={() => create.mutate()}
    >
      {create.isPending ? (
        <LoaderCircleIcon className="animate-spin" />
      ) : (
        <SparklesIcon />
      )}
      {t('profiles.actions.createRecommended')}
    </Button>
  )
}
