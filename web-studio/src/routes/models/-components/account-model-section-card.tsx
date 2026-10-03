import * as React from 'react'
import {
  EyeIcon,
  EyeOffIcon,
  PlusIcon,
  RotateCcwIcon,
  Settings2Icon,
  Trash2Icon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '#/components/ui/alert-dialog'
import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
} from '#/components/ui/field'
import { Input } from '#/components/ui/input'
import { Textarea } from '#/components/ui/textarea'
import {
  ACCOUNT_MODEL_PROVIDERS,
  AccountModelValidationError,
  accountModelDraftToSection,
  accountModelSectionToDraft,
  createEmptyAccountModelCredentialDraft,
  createEmptyAccountModelSectionDraft,
} from '#/lib/account-model-config'
import type {
  AccountModelCredentialDraft,
  AccountModelSection,
  AccountModelSectionConfig,
  AccountModelSectionDraft,
} from '#/lib/account-model-config'
import { getErrorMessage } from '#/routes/users/-lib/error'

type AccountModelSectionCardProps = {
  config: AccountModelSectionConfig | undefined
  onReset: () => Promise<void>
  onSave: (value: AccountModelSectionConfig) => Promise<void>
  section: AccountModelSection
}

function sectionTranslationKey(
  section: AccountModelSection,
): 'queryPlanner' | 'vlm' {
  return section === 'query_planner' ? 'queryPlanner' : 'vlm'
}

function updateCredential(
  draft: AccountModelSectionDraft,
  index: number,
  update: Partial<AccountModelCredentialDraft>,
): AccountModelSectionDraft {
  return {
    ...draft,
    credentials: draft.credentials.map((credential, credentialIndex) =>
      credentialIndex === index ? { ...credential, ...update } : credential,
    ),
  }
}

export function AccountModelSectionCard({
  config,
  onReset,
  onSave,
  section,
}: AccountModelSectionCardProps) {
  const { t } = useTranslation('settings')
  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [resetOpen, setResetOpen] = React.useState(false)
  const [draft, setDraft] = React.useState<AccountModelSectionDraft>(() =>
    config
      ? accountModelSectionToDraft(config)
      : createEmptyAccountModelSectionDraft(),
  )
  const [revealedKeys, setRevealedKeys] = React.useState<Set<number>>(
    () => new Set(),
  )
  const [error, setError] = React.useState<string>('')
  const [saving, setSaving] = React.useState(false)
  const [resetting, setResetting] = React.useState(false)
  const translationKey = sectionTranslationKey(section)

  function openEditor(): void {
    setDraft(
      config
        ? accountModelSectionToDraft(config)
        : createEmptyAccountModelSectionDraft(),
    )
    setRevealedKeys(new Set())
    setError('')
    setDialogOpen(true)
  }

  function validationMessage(validationError: AccountModelValidationError) {
    return t(`models.validation.${validationError.code}`, {
      field: validationError.field,
    })
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')

    let value: AccountModelSectionConfig
    try {
      value = accountModelDraftToSection(draft)
    } catch (caught) {
      setError(
        caught instanceof AccountModelValidationError
          ? validationMessage(caught)
          : getErrorMessage(caught),
      )
      return
    }

    setSaving(true)
    try {
      await onSave(value)
      setDialogOpen(false)
    } catch (caught) {
      setError(getErrorMessage(caught))
    } finally {
      setSaving(false)
    }
  }

  async function reset(): Promise<void> {
    setResetting(true)
    setError('')
    try {
      await onReset()
      setResetOpen(false)
    } catch (caught) {
      setError(getErrorMessage(caught))
      setResetOpen(false)
    } finally {
      setResetting(false)
    }
  }

  return (
    <>
      <Card className="gap-0 overflow-hidden">
        <CardHeader className="gap-3 border-b bg-muted/20">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="space-y-1">
              <CardTitle>
                {t(`models.sections.${translationKey}.title`)}
              </CardTitle>
              <CardDescription>
                {t(`models.sections.${translationKey}.description`)}
              </CardDescription>
            </div>
            <Badge variant={config ? 'default' : 'secondary'}>
              {t(config ? 'models.state.explicit' : 'models.state.inherited')}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid min-h-36 content-start gap-3 px-6 py-5">
          {config ? (
            <dl className="grid gap-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">
                  {t('models.fields.model')}
                </dt>
                <dd className="mt-1 break-all font-medium">{config.model}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">
                  {t('models.fields.credentials')}
                </dt>
                <dd className="mt-1 font-medium">
                  {t('models.credentialCount', {
                    count: config.credentials.length,
                  })}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">
                  {t('models.fields.timeout')}
                </dt>
                <dd className="mt-1 font-medium">
                  {config.timeout === undefined
                    ? t('models.state.clusterDefault')
                    : t('models.timeoutSeconds', { value: config.timeout })}
                </dd>
              </div>
            </dl>
          ) : (
            <div className="rounded-md border border-dashed bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
              {t(`models.sections.${translationKey}.inheritedDescription`)}
            </div>
          )}
          {section === 'query_planner' ? (
            <p className="text-xs leading-5 text-muted-foreground">
              {t('models.sections.queryPlanner.precedence')}
            </p>
          ) : null}
          {error && !dialogOpen ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </CardContent>
        <CardFooter className="justify-end gap-2 border-t bg-muted/10 px-6 py-4">
          {config ? (
            <Button
              type="button"
              variant="outline"
              disabled={resetting}
              onClick={() => setResetOpen(true)}
            >
              <RotateCcwIcon />
              {t('models.actions.inherit')}
            </Button>
          ) : null}
          <Button type="button" onClick={openEditor}>
            <Settings2Icon />
            {t(config ? 'models.actions.edit' : 'models.actions.configure')}
          </Button>
        </CardFooter>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>
              {t('models.editor.title', {
                section: t(`models.sections.${translationKey}.title`),
              })}
            </DialogTitle>
            <DialogDescription>
              {t('models.editor.description')}
            </DialogDescription>
          </DialogHeader>
          <form className="grid gap-6" onSubmit={submit}>
            {error ? (
              <Alert variant="destructive">
                <AlertTitle>{t('models.errors.saveFailed')}</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}

            <div className="grid gap-4 md:grid-cols-2">
              <Field>
                <FieldLabel htmlFor={`${section}-model`}>
                  {t('models.fields.model')}
                </FieldLabel>
                <FieldContent>
                  <Input
                    id={`${section}-model`}
                    value={draft.model}
                    onChange={(event) =>
                      setDraft({ ...draft, model: event.target.value })
                    }
                    placeholder={t('models.placeholders.model')}
                  />
                </FieldContent>
              </Field>
              <Field>
                <FieldLabel htmlFor={`${section}-timeout`}>
                  {t('models.fields.timeout')}
                </FieldLabel>
                <FieldContent>
                  <Input
                    id={`${section}-timeout`}
                    type="number"
                    min="0"
                    step="any"
                    value={draft.timeout}
                    onChange={(event) =>
                      setDraft({ ...draft, timeout: event.target.value })
                    }
                    placeholder={t('models.placeholders.clusterDefault')}
                  />
                  <FieldDescription>
                    {t('models.fields.timeoutHint')}
                  </FieldDescription>
                </FieldContent>
              </Field>
            </div>

            <div className="grid gap-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="font-medium">
                    {t('models.fields.credentials')}
                  </h3>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t('models.editor.credentialsHint')}
                  </p>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      credentials: [
                        ...draft.credentials,
                        createEmptyAccountModelCredentialDraft(),
                      ],
                    })
                  }
                >
                  <PlusIcon />
                  {t('models.actions.addCredential')}
                </Button>
              </div>

              {draft.credentials.map((credential, index) => (
                <div
                  key={index}
                  className="grid gap-4 rounded-lg border bg-muted/10 p-4"
                >
                  <div className="flex items-center justify-between gap-3">
                    <h4 className="font-medium">
                      {t('models.credentialTitle', { index: index + 1 })}
                    </h4>
                    <Button
                      type="button"
                      size="icon-sm"
                      variant="ghost"
                      aria-label={t('models.actions.removeCredential', {
                        index: index + 1,
                      })}
                      disabled={draft.credentials.length === 1}
                      onClick={() => {
                        setDraft({
                          ...draft,
                          credentials: draft.credentials.filter(
                            (_, credentialIndex) => credentialIndex !== index,
                          ),
                        })
                        setRevealedKeys(new Set())
                      }}
                    >
                      <Trash2Icon />
                    </Button>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <Field>
                      <FieldLabel htmlFor={`${section}-provider-${index}`}>
                        {t('models.fields.provider')}
                      </FieldLabel>
                      <select
                        id={`${section}-provider-${index}`}
                        className="flex h-9 w-full rounded-md border border-input bg-transparent px-2.5 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
                        value={credential.provider}
                        onChange={(event) =>
                          setDraft(
                            updateCredential(draft, index, {
                              provider: event.target.value,
                            }),
                          )
                        }
                      >
                        {ACCOUNT_MODEL_PROVIDERS.map((provider) => (
                          <option key={provider} value={provider}>
                            {provider}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field>
                      <FieldLabel
                        htmlFor={`${section}-credential-model-${index}`}
                      >
                        {t('models.fields.credentialModel')}
                      </FieldLabel>
                      <Input
                        id={`${section}-credential-model-${index}`}
                        value={credential.model}
                        onChange={(event) =>
                          setDraft(
                            updateCredential(draft, index, {
                              model: event.target.value,
                            }),
                          )
                        }
                        placeholder={t('models.placeholders.outerModel')}
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor={`${section}-api-base-${index}`}>
                        {t('models.fields.apiBase')}
                      </FieldLabel>
                      <Input
                        id={`${section}-api-base-${index}`}
                        value={credential.apiBase}
                        onChange={(event) =>
                          setDraft(
                            updateCredential(draft, index, {
                              apiBase: event.target.value,
                            }),
                          )
                        }
                        placeholder={t('models.placeholders.apiBase')}
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor={`${section}-api-key-${index}`}>
                        {t('models.fields.apiKey')}
                      </FieldLabel>
                      <FieldContent>
                        <div className="flex gap-2">
                          <Input
                            id={`${section}-api-key-${index}`}
                            type={revealedKeys.has(index) ? 'text' : 'password'}
                            autoComplete="new-password"
                            value={credential.apiKey}
                            onChange={(event) =>
                              setDraft(
                                updateCredential(draft, index, {
                                  apiKey: event.target.value,
                                }),
                              )
                            }
                            placeholder={t('models.placeholders.optional')}
                          />
                          <Button
                            type="button"
                            size="icon"
                            variant="outline"
                            aria-label={t(
                              revealedKeys.has(index)
                                ? 'models.actions.hideApiKey'
                                : 'models.actions.revealApiKey',
                            )}
                            onClick={() =>
                              setRevealedKeys((current) => {
                                const next = new Set(current)
                                if (next.has(index)) {
                                  next.delete(index)
                                } else {
                                  next.add(index)
                                }
                                return next
                              })
                            }
                          >
                            {revealedKeys.has(index) ? (
                              <EyeOffIcon />
                            ) : (
                              <EyeIcon />
                            )}
                          </Button>
                        </div>
                        <FieldDescription>
                          {t('models.fields.apiKeyHint')}
                        </FieldDescription>
                      </FieldContent>
                    </Field>
                  </div>

                  <details className="rounded-md border bg-background/70 px-4 py-3">
                    <summary className="cursor-pointer text-sm font-medium">
                      {t('models.fields.advanced')}
                    </summary>
                    <div className="mt-4 grid gap-4 md:grid-cols-2">
                      <Field>
                        <FieldLabel
                          htmlFor={`${section}-credential-id-${index}`}
                        >
                          {t('models.fields.credentialId')}
                        </FieldLabel>
                        <Input
                          id={`${section}-credential-id-${index}`}
                          value={credential.id}
                          onChange={(event) =>
                            setDraft(
                              updateCredential(draft, index, {
                                id: event.target.value,
                              }),
                            )
                          }
                          placeholder={t('models.placeholders.optional')}
                        />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor={`${section}-api-version-${index}`}>
                          {t('models.fields.apiVersion')}
                        </FieldLabel>
                        <Input
                          id={`${section}-api-version-${index}`}
                          value={credential.apiVersion}
                          onChange={(event) =>
                            setDraft(
                              updateCredential(draft, index, {
                                apiVersion: event.target.value,
                              }),
                            )
                          }
                          placeholder={t('models.placeholders.optional')}
                        />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor={`${section}-reasoning-${index}`}>
                          {t('models.fields.reasoningEffort')}
                        </FieldLabel>
                        <Input
                          id={`${section}-reasoning-${index}`}
                          value={credential.reasoningEffort}
                          onChange={(event) =>
                            setDraft(
                              updateCredential(draft, index, {
                                reasoningEffort: event.target.value,
                              }),
                            )
                          }
                          placeholder={t('models.placeholders.optional')}
                        />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor={`${section}-max-tokens-${index}`}>
                          {t('models.fields.maxTokens')}
                        </FieldLabel>
                        <Input
                          id={`${section}-max-tokens-${index}`}
                          type="number"
                          min="1"
                          step="1"
                          value={credential.maxTokens}
                          onChange={(event) =>
                            setDraft(
                              updateCredential(draft, index, {
                                maxTokens: event.target.value,
                              }),
                            )
                          }
                          placeholder={t('models.placeholders.optional')}
                        />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor={`${section}-keepalive-${index}`}>
                          {t('models.fields.keepaliveExpiry')}
                        </FieldLabel>
                        <Input
                          id={`${section}-keepalive-${index}`}
                          type="number"
                          min="0"
                          step="any"
                          value={credential.keepaliveExpiry}
                          onChange={(event) =>
                            setDraft(
                              updateCredential(draft, index, {
                                keepaliveExpiry: event.target.value,
                              }),
                            )
                          }
                          placeholder={t('models.placeholders.optional')}
                        />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor={`${section}-forward-key-${index}`}>
                          {t('models.fields.forwardApiKey')}
                        </FieldLabel>
                        <select
                          id={`${section}-forward-key-${index}`}
                          className="flex h-9 w-full rounded-md border border-input bg-transparent px-2.5 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
                          value={credential.forwardApiKey}
                          onChange={(event) =>
                            setDraft(
                              updateCredential(draft, index, {
                                forwardApiKey: event.target.value as
                                  | ''
                                  | 'false'
                                  | 'true',
                              }),
                            )
                          }
                        >
                          <option value="">
                            {t('models.values.providerDefault')}
                          </option>
                          <option value="true">{t('models.values.yes')}</option>
                          <option value="false">{t('models.values.no')}</option>
                        </select>
                      </Field>
                      <Field>
                        <FieldLabel htmlFor={`${section}-headers-${index}`}>
                          {t('models.fields.extraHeaders')}
                        </FieldLabel>
                        <Textarea
                          id={`${section}-headers-${index}`}
                          value={credential.extraHeaders}
                          onChange={(event) =>
                            setDraft(
                              updateCredential(draft, index, {
                                extraHeaders: event.target.value,
                              }),
                            )
                          }
                          placeholder={t('models.placeholders.jsonObject')}
                          className="min-h-24 font-mono text-xs"
                        />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor={`${section}-body-${index}`}>
                          {t('models.fields.extraRequestBody')}
                        </FieldLabel>
                        <Textarea
                          id={`${section}-body-${index}`}
                          value={credential.extraRequestBody}
                          onChange={(event) =>
                            setDraft(
                              updateCredential(draft, index, {
                                extraRequestBody: event.target.value,
                              }),
                            )
                          }
                          placeholder={t('models.placeholders.jsonObject')}
                          className="min-h-24 font-mono text-xs"
                        />
                      </Field>
                    </div>
                  </details>
                </div>
              ))}
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={saving}
                onClick={() => setDialogOpen(false)}
              >
                {t('models.actions.cancel')}
              </Button>
              <Button type="submit" disabled={saving}>
                {t(saving ? 'models.actions.saving' : 'models.actions.save')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={resetOpen} onOpenChange={setResetOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('models.reset.title', {
                section: t(`models.sections.${translationKey}.title`),
              })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('models.reset.description')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={resetting}>
              {t('models.actions.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={resetting}
              onClick={(event) => {
                event.preventDefault()
                void reset()
              }}
            >
              {t(
                resetting
                  ? 'models.actions.resetting'
                  : 'models.actions.confirmInherit',
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
