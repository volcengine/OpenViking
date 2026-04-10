import * as React from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/hooks/use-app-connection'

import { Button } from '#/components/ui/button'
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from '#/components/ui/card'
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '#/components/ui/dialog'
import {
    Field,
    FieldContent,
    FieldGroup,
    FieldLabel,
    FieldSet,
} from '#/components/ui/field'
import { Input } from '#/components/ui/input'

export function ConnectionDialog() {
    const { t } = useTranslation(['connection', 'common'])
    const {
        connection,
        isConnectionDialogOpen,
        saveConnection,
        serverMode,
        setConnectionDialogOpen,
    } = useAppConnection()
    const [draft, setDraft] = React.useState(connection)
    const [showAdvancedInDevMode, setShowAdvancedInDevMode] = React.useState(false)

    React.useEffect(() => {
        if (isConnectionDialogOpen) {
            setDraft(connection)
            setShowAdvancedInDevMode(false)
        }
    }, [connection, isConnectionDialogOpen])

    const isDevImplicit = serverMode === 'dev-implicit'
    const showIdentityFields = !isDevImplicit || showAdvancedInDevMode

    return (
        <Dialog open={isConnectionDialogOpen} onOpenChange={setConnectionDialogOpen}>
            <DialogContent className='max-w-2xl'>
                <DialogHeader>
                    <DialogTitle>{t('dialog.title', { ns: 'connection' })}</DialogTitle>
                </DialogHeader>

                <FieldSet>
                    <FieldGroup>
                        <Field>
                            <FieldLabel htmlFor='ov-base-url'>{t('fields.baseUrl.label', { ns: 'connection' })}</FieldLabel>
                            <FieldContent>
                                <Input
                                    id='ov-base-url'
                                    placeholder={t('fields.baseUrl.placeholder', { ns: 'connection' })}
                                    value={draft.baseUrl}
                                    onChange={(event) => setDraft((current) => ({ ...current, baseUrl: event.target.value }))}
                                />
                            </FieldContent>
                        </Field>

                        <Card
                            size='sm'
                            className='min-h-56 gap-3 border bg-background/70 shadow-none'
                        >
                            {showIdentityFields ? (
                                <>
                                    <CardHeader className='gap-1.5'>
                                        <CardTitle className='text-sm'>{t('fields.credentials.title', { ns: 'connection' })}</CardTitle>
                                    </CardHeader>
                                    <CardContent className='grid flex-1 content-start gap-3'>
                                        <div className='grid gap-3 md:grid-cols-2'>
                                            <Field>
                                                <FieldLabel htmlFor='ov-account-id'>{t('fields.accountId.label', { ns: 'connection' })}</FieldLabel>
                                                <FieldContent>
                                                    <Input
                                                        id='ov-account-id'
                                                        placeholder={t('fields.accountId.placeholder', { ns: 'connection' })}
                                                        value={draft.accountId}
                                                        onChange={(event) => setDraft((current) => ({ ...current, accountId: event.target.value }))}
                                                    />
                                                </FieldContent>
                                            </Field>
                                            <Field>
                                                <FieldLabel htmlFor='ov-user-id'>{t('fields.userId.label', { ns: 'connection' })}</FieldLabel>
                                                <FieldContent>
                                                    <Input
                                                        id='ov-user-id'
                                                        placeholder={t('fields.userId.placeholder', { ns: 'connection' })}
                                                        value={draft.userId}
                                                        onChange={(event) => setDraft((current) => ({ ...current, userId: event.target.value }))}
                                                    />
                                                </FieldContent>
                                            </Field>
                                        </div>

                                        <Field>
                                            <FieldLabel htmlFor='ov-api-key'>{t('fields.apiKey.label', { ns: 'connection' })}</FieldLabel>
                                            <FieldContent>
                                                <Input
                                                    id='ov-api-key'
                                                    type='password'
                                                    placeholder={t('fields.apiKey.placeholder', { ns: 'connection' })}
                                                    value={draft.apiKey}
                                                    onChange={(event) => setDraft((current) => ({ ...current, apiKey: event.target.value }))}
                                                />
                                            </FieldContent>
                                        </Field>
                                    </CardContent>
                                </>
                            ) : (
                                <>
                                    <CardHeader className='gap-2'>
                                        <CardTitle className='text-sm'>{t('devMode.title', { ns: 'connection' })}</CardTitle>
                                        <CardDescription>
                                            {t('devMode.description', { ns: 'connection' })}
                                        </CardDescription>
                                    </CardHeader>
                                    <CardContent className='flex flex-1 items-end'>
                                        <Button variant='outline' size='sm' onClick={() => setShowAdvancedInDevMode(true)}>
                                            {t('showAdvancedIdentityFields', { ns: 'common', keyPrefix: 'action' })}
                                        </Button>
                                    </CardContent>
                                </>
                            )}
                        </Card>
                    </FieldGroup>
                </FieldSet>

                <DialogFooter>
                    <Button variant='outline' onClick={() => setConnectionDialogOpen(false)}>{t('cancel', { ns: 'common', keyPrefix: 'action' })}</Button>
                    <Button
                        onClick={() => {
                            saveConnection(draft)
                            setConnectionDialogOpen(false)
                        }}
                    >
                        {t('saveConnection', { ns: 'common', keyPrefix: 'action' })}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}