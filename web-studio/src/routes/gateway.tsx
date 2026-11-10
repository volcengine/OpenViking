import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/gateway')({
  beforeLoad: ({ location }) => {
    throw redirect({
      replace: true,
      href: `/admin${location.pathname}${location.searchStr}${location.hash ? `#${location.hash}` : ''}`,
    })
  },
})
