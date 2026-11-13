import type { ComponentProps } from 'react'

export function OpenVikingMark(props: ComponentProps<'svg'>) {
  return (
    <svg
      viewBox="0 0 100 100"
      fill="currentColor"
      aria-hidden="true"
      {...props}
    >
      <path d="M20.5 84H31L70 16.45H67ZM37 84 76 16.45V84Z" />
    </svg>
  )
}
