// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TagFilterInput } from './tag-filter-input'

afterEach(cleanup)

describe('TagFilterInput', () => {
  it('reports invalid tags and only publishes strict key=value values', () => {
    const onChange = vi.fn()
    const placeholder = 'env=prod'
    render(
      <TagFilterInput
        ariaLabel="Search tags"
        invalidMessage="Use key=value"
        onChange={onChange}
        placeholder={placeholder}
        value={[]}
      />,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'Search tags' }), {
      target: { value: 'invalid-tag' },
    })
    expect(screen.getByText('Use key=value')).toBeDefined()
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.change(screen.getByRole('textbox', { name: 'Search tags' }), {
      target: { value: 'env=prod, team=search' },
    })
    expect(screen.queryByText('Use key=value')).toBeNull()
    expect(onChange).toHaveBeenLastCalledWith(['env=prod', 'team=search'])
  })
})
