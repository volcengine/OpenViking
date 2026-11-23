import { ActivityIcon } from 'lucide-react'

type EmptyLogsStateProps = {
  description: string
  title: string
}

export function EmptyLogsState({ description, title }: EmptyLogsStateProps) {
  return (
    <div className="flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center">
      <div className="flex size-11 items-center justify-center rounded-lg border bg-muted/30 text-muted-foreground">
        <ActivityIcon className="size-5" />
      </div>
      <div>
        <p className="font-medium">{title}</p>
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}
