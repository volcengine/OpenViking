import * as React from 'react'

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
                    <DialogTitle>连接与身份</DialogTitle>
                </DialogHeader>

                <FieldSet>
                    <FieldGroup>
                        <Field>
                            <FieldLabel htmlFor='ov-base-url'>服务地址</FieldLabel>
                            <FieldContent>
                                <Input
                                    id='ov-base-url'
                                    placeholder='http://127.0.0.1:1933'
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
                                        <CardTitle className='text-sm'>身份与凭证</CardTitle>
                                    </CardHeader>
                                    <CardContent className='grid flex-1 content-start gap-3'>
                                        <div className='grid gap-3 md:grid-cols-2'>
                                            <Field>
                                                <FieldLabel htmlFor='ov-account-id'>Account</FieldLabel>
                                                <FieldContent>
                                                    <Input
                                                        id='ov-account-id'
                                                        placeholder='default'
                                                        value={draft.accountId}
                                                        onChange={(event) => setDraft((current) => ({ ...current, accountId: event.target.value }))}
                                                    />
                                                </FieldContent>
                                            </Field>
                                            <Field>
                                                <FieldLabel htmlFor='ov-user-id'>User</FieldLabel>
                                                <FieldContent>
                                                    <Input
                                                        id='ov-user-id'
                                                        placeholder='default'
                                                        value={draft.userId}
                                                        onChange={(event) => setDraft((current) => ({ ...current, userId: event.target.value }))}
                                                    />
                                                </FieldContent>
                                            </Field>
                                        </div>

                                        <Field>
                                            <FieldLabel htmlFor='ov-api-key'>API Key</FieldLabel>
                                            <FieldContent>
                                                <Input
                                                    id='ov-api-key'
                                                    type='password'
                                                    placeholder='输入 X-API-Key 或 Bearer token'
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
                                        <CardTitle className='text-sm'>已检测到开发模式</CardTitle>
                                        <CardDescription>
                                            当前环境下服务端会使用隐式身份，通常不需要填写 API Key、Account 或 User 字段。
                                        </CardDescription>
                                    </CardHeader>
                                    <CardContent className='flex flex-1 items-end'>
                                        <Button variant='outline' size='sm' onClick={() => setShowAdvancedInDevMode(true)}>
                                            显示高级身份字段
                                        </Button>
                                    </CardContent>
                                </>
                            )}
                        </Card>
                    </FieldGroup>
                </FieldSet>

                <DialogFooter>
                    <Button variant='outline' onClick={() => setConnectionDialogOpen(false)}>取消</Button>
                    <Button
                        onClick={() => {
                            saveConnection(draft)
                            setConnectionDialogOpen(false)
                        }}
                    >
                        保存连接
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}