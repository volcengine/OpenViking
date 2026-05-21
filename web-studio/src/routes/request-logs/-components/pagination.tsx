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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { cn } from '#/lib/utils'

import { PAGE_SIZE_OPTIONS } from '../-constants/audit'

type RequestLogPaginationProps = {
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  page: number
  pageCount: number
  pageSize: number
  total: number
}

export function RequestLogPagination({
  onPageChange,
  onPageSizeChange,
  page,
  pageCount,
  pageSize,
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
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm text-muted-foreground">
          {t('pagination.summary', { page, pageCount, total })}
        </p>
        <Select
          value={String(pageSize)}
          onValueChange={(value) => onPageSizeChange(Number(value))}
        >
          <SelectTrigger size="sm" aria-label={t('pagination.pageSize')}>
            <SelectValue>
              {t('pagination.pageSizeValue', { count: pageSize })}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZE_OPTIONS.map((option) => (
              <SelectItem key={option} value={String(option)}>
                {t('pagination.pageSizeValue', { count: option })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
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
