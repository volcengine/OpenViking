import { useQuery } from '@tanstack/react-query';
import { Activity, Clock, Database, FileText, Monitor, ToggleLeft, ToggleRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { ovClient } from '@/lib/ov-client/client';

interface WatcherStatus {
  tool_name: string;
  watch_dir: string | null;
  file_pattern: string | null;
  enabled: boolean;
  running: boolean;
  cursor_count: number;
  batch_trigger_lines: number | null;
  batch_trigger_seconds: number | null;
}

interface DaemonStatus {
  enabled: boolean;
  running: boolean;
  watchers: WatcherStatus[];
  available_tools: string[];
  db_path: string | null;
}

async function fetchDaemonStatus(): Promise<DaemonStatus> {
  const response = await ovClient.instance.get('/api/v1/daemon/status');
  return response.data as DaemonStatus;
}

function ToolBadge({ toolName }: { toolName: string }) {
  const colors: Record<string, string> = {
    claude_code: 'bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-200 dark:border-orange-800',
    generic_jsonl: 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800',
    aider: 'bg-green-500/10 text-green-600 dark:text-green-400 border-green-200 dark:border-green-800',
    cursor: 'bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-200 dark:border-purple-800',
    continue_dev: 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-200 dark:border-cyan-800',
  };
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium border ${colors[toolName] || 'bg-gray-100 text-gray-600 border-gray-200'}`}>
      {toolName}
    </span>
  );
}

export function DaemonStatusCard() {
  const { t } = useTranslation();

  const { data, isLoading, error } = useQuery({
    queryKey: ['daemon-status'],
    queryFn: fetchDaemonStatus,
    refetchInterval: 30000,
  });

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-4 w-4" />
            <Skeleton className="h-5 w-32" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-4 w-full mb-2" />
          <Skeleton className="h-4 w-3/4" />
        </CardContent>
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-muted-foreground">
            <Activity className="h-4 w-4" />
            {t('daemon.status')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Failed to load daemon status</p>
        </CardContent>
      </Card>
    );
  }

  const statusColor = data.enabled && data.running
    ? 'bg-green-500'
    : data.enabled
    ? 'bg-yellow-500'
    : 'bg-gray-400';

  const statusLabel = data.enabled && data.running
    ? t('daemon.running')
    : data.enabled
    ? t('daemon.stopped')
    : t('daemon.disabled');

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4" />
            {t('daemon.status')}
          </div>
          <Badge variant={data.enabled ? 'default' : 'secondary'} className={statusColor}>
            {statusLabel}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Global Status */}
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground flex items-center gap-1">
            {data.enabled ? <ToggleRight className="h-4 w-4" /> : <ToggleLeft className="h-4 w-4" />}
            {t('daemon.enabled')}
          </span>
          <span className="font-medium">
            {data.watchers.length} {t('daemon.watcherCount')}
          </span>
        </div>

        {/* Watcher List */}
        {data.watchers.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              {t('daemon.activeWatchers')}
            </div>
            {data.watchers.map((w, i) => (
              <div
                key={`${w.tool_name}-${i}`}
                className="flex items-center justify-between rounded-md border px-3 py-2"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <ToolBadge toolName={w.tool_name} />
                  {w.watch_dir && (
                    <span className="font-mono text-xs text-muted-foreground truncate max-w-[160px]" title={w.watch_dir}>
                      {w.watch_dir.split(/[\\/]/).pop() || w.watch_dir}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground shrink-0 ml-2">
                  <span className="flex items-center gap-1">
                    <Database className="h-3 w-3" />
                    {w.cursor_count}
                  </span>
                  {w.batch_trigger_lines && (
                    <span className="flex items-center gap-1">
                      <FileText className="h-3 w-3" />
                      {w.batch_trigger_lines}
                    </span>
                  )}
                  {w.batch_trigger_seconds && (
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {w.batch_trigger_seconds}s
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Available Tools */}
        {data.available_tools.length > 0 && (
          <div className="pt-2 border-t">
            <div className="text-xs font-medium text-muted-foreground mb-1.5">
              <Monitor className="h-3 w-3 inline mr-1" />
              {t('daemon.availableTools')}
            </div>
            <div className="flex flex-wrap gap-1">
              {data.available_tools.map((tool) => (
                <ToolBadge key={tool} toolName={tool} />
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
