import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'

type Props = {
  page: number
  pageCount: number
  pageSize: number
  total: number
  setPage: (page: number) => void
  setPageSize: (size: number) => void
}

export function UserPagination({
  page,
  pageCount,
  pageSize,
  total,
  setPage,
  setPageSize,
}: Props) {
  const { t } = useTranslation('settings')
  return (
    <nav
      aria-label={t('userList.pagination')}
      className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3"
    >
      <p className="text-sm text-muted-foreground" aria-live="polite">
        {t('userList.summary', { page, pageCount, total })}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={String(pageSize)}
          onValueChange={(value) => {
            if (value) setPageSize(Number(value))
          }}
        >
          <SelectTrigger size="sm" aria-label={t('userList.pageSize')}>
            <SelectValue>
              {t('userList.pageSizeValue', { count: pageSize })}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {[20, 50, 100].map((size) => (
              <SelectItem key={size} value={String(size)}>
                {t('userList.pageSizeValue', { count: size })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="sm"
          disabled={page === 1}
          onClick={() => setPage(1)}
        >
          {t('userList.first')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={page === 1}
          onClick={() => setPage(page - 1)}
        >
          {t('userList.previous')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={page === pageCount}
          onClick={() => setPage(page + 1)}
        >
          {t('userList.next')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={page === pageCount}
          onClick={() => setPage(pageCount)}
        >
          {t('userList.last')}
        </Button>
      </div>
    </nav>
  )
}
