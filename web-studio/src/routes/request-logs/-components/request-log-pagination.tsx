import * as React from 'react'
import { useTranslation } from 'react-i18next'

import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from '#/components/ui/pagination'
import { cn } from '#/lib/utils'

type RequestLogPaginationProps = {
  onPageChange: (page: number) => void
  page: number
  pageCount: number
  total: number
}

export function RequestLogPagination({
  onPageChange,
  page,
  pageCount,
  total,
}: RequestLogPaginationProps) {
  const { t } = useTranslation('requestLogs')
  const pages = React.useMemo(() => {
    const start = Math.max(1, page - 2)
    const end = Math.min(pageCount, start + 4)
    return Array.from({ length: end - start + 1 }, (_, index) => start + index)
  }, [page, pageCount])

  return (
    <div className="flex flex-col gap-3 border-t px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-muted-foreground">
        {t('pagination.summary', { page, pageCount, total })}
      </p>
      <Pagination className="mx-0 w-auto justify-start sm:justify-end">
        <PaginationContent>
          <PaginationItem>
            <PaginationPrevious
              href="#"
              text={t('pagination.previous')}
              aria-disabled={page <= 1}
              className={cn(page <= 1 && 'pointer-events-none opacity-50')}
              onClick={(event) => {
                event.preventDefault()
                if (page > 1) onPageChange(page - 1)
              }}
            />
          </PaginationItem>
          {pages.map((item) => (
            <PaginationItem key={item}>
              <PaginationLink
                href="#"
                isActive={item === page}
                onClick={(event) => {
                  event.preventDefault()
                  onPageChange(item)
                }}
              >
                {item}
              </PaginationLink>
            </PaginationItem>
          ))}
          <PaginationItem>
            <PaginationNext
              href="#"
              text={t('pagination.next')}
              aria-disabled={page >= pageCount}
              className={cn(
                page >= pageCount && 'pointer-events-none opacity-50',
              )}
              onClick={(event) => {
                event.preventDefault()
                if (page < pageCount) onPageChange(page + 1)
              }}
            />
          </PaginationItem>
        </PaginationContent>
      </Pagination>
    </div>
  )
}
