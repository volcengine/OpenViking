// @vitest-environment jsdom
import * as React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ConfirmDialog } from './confirm-dialog'
import { KeyValueEditor } from './key-value-editor'
import { NumberInput } from './number-input'
import { TagInput, splitTags } from './tag-input'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(cleanup)

const MODELS = 'models'
const NUMBER = 'n'
const TITLE = 'Revoke?'
const DESCRIPTION = 'Clients lose access.'

function Controlled<T>({
  initial,
  children,
}: {
  initial: T
  children: (value: T, setValue: (value: T) => void) => React.ReactNode
}) {
  const [value, setValue] = React.useState(initial)
  return (
    <>
      {children(value, setValue)}
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  )
}

const current = () => JSON.parse(screen.getByTestId('value').textContent)

describe('TagInput', () => {
  it('adds on Enter, splits pasted lists and removes with Backspace', () => {
    render(
      <Controlled initial={['a']}>
        {(value, setValue) => (
          <TagInput
            value={value}
            onChange={setValue}
            suggestions={['a', 'gpt-5']}
            aria-label={MODELS}
          />
        )}
      </Controlled>,
    )
    const input = screen.getByLabelText(MODELS)
    fireEvent.change(input, { target: { value: 'b' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(current()).toEqual(['a', 'b'])

    fireEvent.paste(input, {
      clipboardData: { getData: () => 'c, d\nb' },
    })
    expect(current()).toEqual(['a', 'b', 'c', 'd'])

    fireEvent.keyDown(input, { key: 'Backspace' })
    expect(current()).toEqual(['a', 'b', 'c'])

    fireEvent.click(screen.getByText('gpt-5'))
    expect(current()).toEqual(['a', 'b', 'c', 'gpt-5'])
  })

  it('splits on commas and new lines', () => {
    expect(splitTags(' a,b\n\n c ,')).toEqual(['a', 'b', 'c'])
  })
})

describe('KeyValueEditor', () => {
  it('emits named rows and flags repeated names', () => {
    render(
      <Controlled initial={{ fast: 'gpt-5-mini' } as Record<string, string>}>
        {(value, setValue) => (
          <KeyValueEditor
            value={value}
            onChange={setValue}
            keyPlaceholder="name"
            valuePlaceholder="model"
          />
        )}
      </Controlled>,
    )
    fireEvent.click(screen.getByText('keyValue.add'))
    const names = screen.getAllByLabelText('name')
    const models = screen.getAllByLabelText('model')
    fireEvent.change(models[1], { target: { value: 'gpt-5' } })
    expect(current()).toEqual({ fast: 'gpt-5-mini' })
    fireEvent.change(names[1], { target: { value: 'smart' } })
    expect(current()).toEqual({ fast: 'gpt-5-mini', smart: 'gpt-5' })
    fireEvent.change(names[1], { target: { value: 'fast' } })
    expect(current()).toEqual({ fast: 'gpt-5' })
    expect(screen.getByText('keyValue.duplicate')).toBeTruthy()
  })

  it('marks stored secrets and emits numbers', () => {
    render(
      <>
        <KeyValueEditor
          value={{ Authorization: '' }}
          onChange={() => {}}
          storedKeys={['Authorization']}
          maskValues
          keyPlaceholder="header"
          valuePlaceholder="value"
        />
        <Controlled initial={{ m: 128000 } as Record<string, number>}>
          {(value, setValue) => (
            <KeyValueEditor
              numeric
              value={value}
              onChange={setValue}
              keyPlaceholder="model"
              valuePlaceholder="tokens"
            />
          )}
        </Controlled>
      </>,
    )
    const secret = screen.getByLabelText<HTMLInputElement>('value')
    expect(secret.placeholder).toBe('field.storedSecret')
    expect(secret.type).toBe('password')
    fireEvent.change(screen.getByLabelText('tokens'), {
      target: { value: '200000' },
    })
    expect(current()).toEqual({ m: 200000 })
  })
})

describe('NumberInput', () => {
  it('reports NaN while empty and follows outside changes', () => {
    const onChange = vi.fn()
    const view = render(
      <NumberInput aria-label={NUMBER} value={1600} onChange={onChange} />,
    )
    const input = screen.getByLabelText<HTMLInputElement>(NUMBER)
    fireEvent.change(input, { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith(Number.NaN)
    view.rerender(
      <NumberInput aria-label={NUMBER} value={64} onChange={onChange} />,
    )
    expect(input.value).toBe('64')
  })
})

describe('ConfirmDialog', () => {
  function renderDialog(onConfirm: () => Promise<unknown>) {
    const onOpenChange = vi.fn()
    render(
      <ConfirmDialog
        open
        onOpenChange={onOpenChange}
        title={TITLE}
        description={DESCRIPTION}
        confirmLabel="Revoke"
        onConfirm={onConfirm}
      />,
    )
    fireEvent.click(screen.getByText('Revoke'))
    return onOpenChange
  }

  it('closes after the action succeeds', async () => {
    const onOpenChange = renderDialog(() => Promise.resolve())
    await vi.waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })

  it('stays open when the action fails', async () => {
    const onConfirm = vi.fn(() => Promise.reject(new Error('no')))
    const onOpenChange = renderDialog(onConfirm)
    await vi.waitFor(() => expect(onConfirm).toHaveBeenCalled())
    await Promise.resolve()
    expect(onOpenChange).not.toHaveBeenCalled()
  })
})
