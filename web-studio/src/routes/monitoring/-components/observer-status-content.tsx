import * as React from 'react'
import { useTranslation } from 'react-i18next'

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { localizeObserverStatusBlocks } from '../-lib/localize-observer-status'
import { parseObserverStatus } from '../-lib/parse-status'

export function ObserverStatusContent({ status }: { status: string }) {
  const { t } = useTranslation('monitoringPage')
  const blocks = React.useMemo(
    () => localizeObserverStatusBlocks(parseObserverStatus(status), t),
    [status, t],
  )

  if (blocks.length === 0) {
    return <p className="text-sm text-muted-foreground">{t('detail.noData')}</p>
  }

  return (
    <div className="grid gap-4">
      {blocks.map((block, blockIndex) =>
        block.kind === 'text' ? (
          <p
            key={`${block.value}-${blockIndex}`}
            className="rounded-lg border bg-muted/20 px-3 py-2 text-sm text-muted-foreground"
          >
            {block.value}
          </p>
        ) : (
          <div
            key={`table-${blockIndex}`}
            className="overflow-x-auto rounded-lg border"
          >
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/20 hover:bg-muted/20">
                  {block.headers.map((header, headerIndex) => (
                    <TableHead
                      key={`${header}-${headerIndex}`}
                      className="whitespace-nowrap"
                    >
                      {header}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {block.rows.map((row, rowIndex) => (
                  <TableRow key={`row-${rowIndex}`}>
                    {row.map((cell, cellIndex) => (
                      <TableCell
                        key={`${cell}-${cellIndex}`}
                        className="whitespace-nowrap font-mono text-xs"
                      >
                        {cell}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ),
      )}
    </div>
  )
}
