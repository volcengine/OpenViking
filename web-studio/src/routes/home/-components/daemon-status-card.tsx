import { useQuery } from '@tanstack/react-query';
import { Activity, Clock, Database, FileText, ToggleLeft, ToggleRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { ovClient } from '@/lib/ov-client/client';

interface DaemonStatus {
  enabled: boolean;
  running: boolean;
  watch_dir: string | null;
  db_path: string | null;
  batch_trigger_lines: number;
  batch_trigger_seconds: number;
  cursor_count: number;
  last_flush_time: string | null;
}

async function fetchDaemonStatus(): Promise<DaemonStatus> {
  const response = await ovClient.instance.get('/api/v1/daemon/status');
  return response.data as DaemonStatus;
}

export function DaemonStatusCard() {
  const { t } = useTranslation();
  
  const { data, isLoading, error } = useQuery({
    queryKey: ['daemon-status'],
    queryFn: fetchDaemonStatus,
    refetchInterval: 30000, // Refresh every 30 seconds
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
      <CardContent className="space-y-3">
        {/* Enabled/Running Status */}
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground flex items-center gap-1">
            {data.enabled ? <ToggleRight className="h-4 w-4" /> : <ToggleLeft className="h-4 w-4" />}
            {t('daemon.enabled')}
          </span>
          <span className="font-medium">{data.enabled ? 'Yes' : 'No'}</span>
        </div>

        {/* Watch Directory */}
        {data.watch_dir && (
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground flex items-center gap-1">
              <Database className="h-4 w-4" />
              {t('daemon.watchDir')}
            </span>
            <span className="font-mono text-xs truncate max-w-[200px]" title={data.watch_dir}>
              {data.watch_dir.split('/').pop() || data.watch_dir}
            </span>
          </div>
        )}

        {/* Batch Settings */}
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div className="flex items-center gap-1 text-muted-foreground">
            <FileText className="h-4 w-4" />
            {t('daemon.batchLines')}
          </div>
          <div className="text-right font-medium">{data.batch_trigger_lines}</div>
          
          <div className="flex items-center gap-1 text-muted-foreground">
            <Clock className="h-4 w-4" />
            {t('daemon.batchSeconds')}
          </div>
          <div className="text-right font-medium">{data.batch_trigger_seconds}s</div>
        </div>

        {/* Cursor Count */}
        <div className="flex items-center justify-between text-sm pt-2 border-t">
          <span className="text-muted-foreground">{t('daemon.cursorCount')}</span>
          <span className="font-medium">{data.cursor_count}</span>
        </div>

        {/* Last Flush Time */}
        {data.last_flush_time && (
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{t('daemon.lastFlush')}</span>
            <span className="font-medium text-xs">
              {new Date(data.last_flush_time).toLocaleTimeString()}
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
