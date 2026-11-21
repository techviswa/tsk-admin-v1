import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { POS_OPERATIONS, sourceValue, displayValue, recordCurrency } from '@/lib/posOperations';
import OperationalRecordDetails from '@/components/OperationalRecordDetails';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

const PAGE_SIZE = 50;

export default function POSOperationsPage() {
  const { resource } = useParams();
  const { selectedBusiness } = useBusiness();
  const config = POS_OPERATIONS[resource];
  const businessId = selectedBusiness?.id;
  const scope = `${businessId || ''}:${resource}`;
  const version = useRef(0);
  const [result, setResult] = useState(null);
  const [filter, setFilter] = useState({ scope: '', search: '', offset: 0 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState(null);
  const search = filter.scope === scope ? filter.search : '';
  const offset = filter.scope === scope ? filter.offset : 0;
  const data = result?.scope === scope ? result.data : null;
  const selected = detail?.scope === scope ? detail.record : null;
  const pending = ['pending', 'running', 'retrying'].includes(data?.refresh_job?.status);

  const load = useCallback(async () => {
    const current = ++version.current;
    if (!businessId) return;
    setLoading(true);
    setError('');
    try {
      const response = await api.get(`/pos-admin/${resource}`, { params: { business_id: businessId, search: search || undefined, limit: PAGE_SIZE, offset } });
      if (current === version.current) setResult({ scope, data: response.data });
    } catch (err) {
      if (current === version.current) setError(formatApiError(err));
    } finally {
      if (current === version.current) setLoading(false);
    }
  }, [businessId, resource, scope, search, offset]);

  useEffect(() => {
    load();
    return () => { version.current += 1; };
  }, [load]);

  useEffect(() => {
    if (!businessId) return undefined;
    const timer = setInterval(load, pending ? 3000 : 30000);
    return () => clearInterval(timer);
  }, [businessId, pending, load]);

  const refresh = async () => {
    const current = ++version.current;
    setLoading(true);
    setError('');
    try {
      await api.post(`/pos-bridge/queue/${resource}`, null, { params: { business_id: businessId } });
      if (current === version.current) await load();
    } catch (err) {
      if (current === version.current) { setError(formatApiError(err)); setLoading(false); }
    }
  };

  if (!config) return <p>Unknown POS module.</p>;
  if (!businessId) return <div className="p-12 text-center"><h1 className="text-xl font-semibold">{config.title}</h1><p className="mt-2 text-zinc-500">Select a business to view its POS records.</p></div>;

  const rows = data?.records || [];
  const total = data?.pagination?.total ?? data?.summary?.total ?? 0;
  return (
    <div className="space-y-5" data-testid="pos-operations-page">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><h1 className="text-2xl font-semibold">{config.title}</h1><p className="mt-1 text-sm text-zinc-500">{selectedBusiness.name} · Records synchronized from POS. Manage operational changes in POS.</p></div>
        <Button variant="outline" onClick={refresh} disabled={loading || pending || selectedBusiness.pos_connection_state === 'setup_required'}>Refresh from POS</Button>
      </div>
      {error && <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>}
      {pending && <p role="status" className="rounded border border-blue-200 bg-blue-50 p-3 text-sm">POS refresh {data.refresh_job.status}. Showing the last imported records until it completes.</p>}
      {data?.refresh_job?.status === 'failed' && <p role="alert" className="rounded border border-amber-200 bg-amber-50 p-3 text-sm">The last POS refresh failed. These records may be out of date. Check POS Bridge for details, then retry.</p>}
      {data?.refresh_job?.status === 'synced' && data.refresh_job.finished_at && <p className="text-xs text-zinc-500">Last POS refresh: {displayValue(data.refresh_job.finished_at, 'date')}</p>}
      <Input aria-label="Search records" placeholder="Search records…" value={search} onChange={event => setFilter({ scope, search: event.target.value, offset: 0 })} className="max-w-sm" />
      <div className="overflow-x-auto rounded-lg border bg-white">
        <Table><TableHeader><TableRow>{config.columns.map(([label]) => <TableHead key={label}>{label}</TableHead>)}<TableHead>Status</TableHead><TableHead>Details</TableHead></TableRow></TableHeader>
          <TableBody>
            {loading && !data ? <TableRow><TableCell colSpan={config.columns.length + 2}>Loading POS records…</TableCell></TableRow> : rows.length === 0 ? <TableRow><TableCell colSpan={config.columns.length + 2} className="py-10 text-center text-zinc-500">{pending ? 'Waiting for POS data…' : 'No imported records match this view.'}</TableCell></TableRow> : rows.map(row => (
              <TableRow key={row.id}>{config.columns.map(([label, key, format]) => <TableCell key={label}>{displayValue(sourceValue(row, key), format, recordCurrency(row))}</TableCell>)}<TableCell><Badge variant="outline">{row.status?.replace(/_/g, ' ') || 'Unknown'}</Badge></TableCell><TableCell><Button variant="ghost" onClick={() => setDetail({ scope, record: row })}>View</Button></TableCell></TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-between gap-3 text-sm text-zinc-500"><span>{total ? `${offset + 1}–${offset + rows.length} of ${total}` : '0 records'}</span><div className="flex gap-2"><Button variant="outline" disabled={loading || !offset} onClick={() => setFilter({ scope, search, offset: Math.max(0, offset - PAGE_SIZE) })}>Previous</Button><Button variant="outline" disabled={loading || !data?.pagination?.has_more} onClick={() => setFilter({ scope, search, offset: offset + PAGE_SIZE })}>Next</Button></div></div>
      <Sheet open={Boolean(selected)} onOpenChange={open => { if (!open) setDetail(null); }}><SheetContent className="overflow-y-auto sm:max-w-xl"><SheetHeader><SheetTitle>{selected?.title || config.title}</SheetTitle></SheetHeader>{selected && <OperationalRecordDetails record={selected} config={config} />}</SheetContent></Sheet>
    </div>
  );
}
