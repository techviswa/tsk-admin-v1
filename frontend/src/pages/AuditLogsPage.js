import { useState, useEffect, useCallback } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import { useAuth } from '@/contexts/AuthContext';
import { hasPlatformPermission } from '@/lib/permissions';
import { entityLabel } from '@/lib/posOperations';
import api, { formatApiError } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Activity, ChevronLeft, ChevronRight } from 'lucide-react';

const ACTION_COLORS = {
  created: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  updated: 'bg-blue-100 text-blue-700 border-blue-200',
  deleted: 'bg-red-100 text-red-700 border-red-200',
  toggled: 'bg-violet-100 text-violet-700 border-violet-200',
  login: 'bg-amber-100 text-amber-700 border-amber-200',
  disabled: 'bg-zinc-100 text-zinc-700 border-zinc-200',
};

export default function AuditLogsPage() {
  const { selectedBusiness } = useBusiness();
  const { user } = useAuth();
  const canReadAll = hasPlatformPermission(user, 'audit.read');
  const [logs, setLogs] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [entityFilter, setEntityFilter] = useState('all');
  const [error, setError] = useState('');
  const limit = 20;

  const fetchLogs = useCallback(async (signal) => {
    setError('');
    try {
      let url, params;
      if (selectedBusiness) {
        url = `/audit-logs/business/${selectedBusiness.id}`;
        params = { limit, skip: page * limit };
      } else if (canReadAll) {
        url = '/audit-logs';
        params = { limit, skip: page * limit };
      } else {
        setLoading(false);
        return;
      }
      params.entity_type = entityFilter === 'all' ? undefined : entityFilter;
      const { data } = await api.get(url, { params, signal });
      if (signal?.aborted) return;
      setLogs(data.logs || []);
      setTotal(data.total || 0);
    } catch (err) { if (!signal?.aborted) setError(formatApiError(err)); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [selectedBusiness, canReadAll, page, entityFilter]);

  useEffect(() => { setPage(0); }, [selectedBusiness, entityFilter]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetchLogs(controller.signal);
    return () => controller.abort();
  }, [fetchLogs]);

  const totalPages = Math.ceil(total / limit);
  const entities = ['all', 'business', 'outlet', 'module', 'user', 'setting', 'feature_flag', 'integration', 'auth'];

  return (
    <div className="space-y-6" data-testid="audit-logs-page">
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Audit Logs</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            {selectedBusiness ? `Activity log for ${selectedBusiness.name}` : 'Platform-wide activity log'}
          </p>
        </div>
        <Select value={entityFilter} onValueChange={setEntityFilter}>
          <SelectTrigger className="w-44" data-testid="audit-entity-filter"><SelectValue placeholder="Filter by entity" /></SelectTrigger>
          <SelectContent>
            {entities.map(e => <SelectItem key={e} value={e} className="capitalize">{e === 'all' ? 'All Entities' : e.replace(/_/g, ' ')}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
        {error && <div role="alert" className="p-3 text-sm text-red-700">Audit logs could not load: {error} <Button variant="outline" size="sm" onClick={() => fetchLogs()}>Retry</Button></div>}
        <Table>
          <TableHeader>
            <TableRow className="bg-zinc-50 hover:bg-zinc-50">
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Timestamp</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">User</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Action</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Entity</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Details</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={5} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
            ) : error ? null : logs.length === 0 ? (
              <TableRow><TableCell colSpan={5} className="text-center py-12 text-zinc-400">No audit logs found</TableCell></TableRow>
            ) : logs.map(log => (
              <TableRow key={log.id} className="hover:bg-zinc-50/50">
                <TableCell className="text-xs text-zinc-500 py-3 whitespace-nowrap font-mono">{new Date(log.created_at).toLocaleString()}</TableCell>
                <TableCell className="text-sm text-zinc-700 py-3">{log.user_email || 'System'}</TableCell>
                <TableCell className="py-3"><Badge className={`text-[11px] ${ACTION_COLORS[log.action] || 'bg-zinc-100 text-zinc-600'}`}>{log.action}</Badge></TableCell>
                <TableCell className="py-3"><Badge variant="outline" className="text-[11px]">{entityLabel(log.entity_type)}</Badge></TableCell>
                <TableCell className="text-xs text-zinc-500 py-3 max-w-[320px]">
                  {log.details && Object.keys(log.details).length > 0 ? <details><summary className="cursor-pointer">View changes</summary><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(log.details, null, 2)}</pre></details> : 'No additional details'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-zinc-200">
            <span className="text-xs text-zinc-500">Page {page + 1} of {totalPages} ({total} total)</span>
            <div className="flex items-center gap-1">
              <Button variant="outline" size="sm" onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0} className="h-7 w-7 p-0" data-testid="audit-prev-btn"><ChevronLeft className="h-4 w-4" /></Button>
              <Button variant="outline" size="sm" onClick={() => setPage(p => p + 1)} disabled={page >= totalPages - 1} className="h-7 w-7 p-0" data-testid="audit-next-btn"><ChevronRight className="h-4 w-4" /></Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
