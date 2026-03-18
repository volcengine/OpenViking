import React from 'react'

export interface TableProps {
  children: React.ReactNode
  className?: string
  striped?: boolean
}

export const Table: React.FC<TableProps> = ({
  children,
  className = '',
  striped = true
}) => {
  return (
    <div className="overflow-x-auto">
      <table
        className={`min-w-full ${striped ? 'divide-y divide-gray-200' : ''} ${className}`}
      >
        {children}
      </table>
    </div>
  )
}

export interface TableHeaderProps {
  children: React.ReactNode
  className?: string
}

export const TableHeader: React.FC<TableHeaderProps> = ({
  children,
  className = ''
}) => {
  return (
    <thead className={className}>
      {children}
    </thead>
  )
}

export interface TableBodyProps {
  children: React.ReactNode
  className?: string
}

export const TableBody: React.FC<TableBodyProps> = ({
  children,
  className = ''
}) => {
  return (
    <tbody
      className={`divide-y divide-gray-200 ${className}`}
    >
      {children}
    </tbody>
  )
}

export interface TableRowProps {
  children: React.ReactNode
  className?: string
  onClick?: () => void
  hover?: boolean
}

export const TableRow: React.FC<TableRowProps> = ({
  children,
  className = '',
  onClick,
  hover = true
}) => {
  return (
    <tr
      onClick={onClick}
      className={`
        ${hover ? 'hover:bg-gray-50' : ''}
        ${className}
      `}
    >
      {children}
    </tr>
  )
}

export interface TableCellProps {
  children: React.ReactNode
  className?: string
  header?: boolean
}

export const TableCell: React.FC<TableCellProps> = ({
  children,
  className = '',
  header = false
}) => {
  const cellClass = header
    ? `px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider ${className}`
    : `px-6 py-4 whitespace-nowrap ${className}`

  return header ? (
    <th className={cellClass}>{children}</th>
  ) : (
    <td className={cellClass}>{children}</td>
  )
}

export default Table
