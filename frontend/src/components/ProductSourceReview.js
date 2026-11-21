import { useEffect, useState } from 'react';
import api, { formatApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';

export default function ProductSourceReview({ product, onClose, onDone, resource = 'products' }) {
  const noun = resource === 'outlets' ? 'outlet' : 'product';
  const [rows, setRows] = useState([]);
  const [search, setSearch] = useState('');
  const [source, setSource] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [archive, setArchive] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api.get(`/pos-bridge/proxy/${resource}`, { params: { business_id: product.business_id } })
      .then(({ data }) => { if (active) setRows(data.rows || []); })
      .catch(error => { if (active) setError(formatApiError(error)); });
    return () => { active = false; };
  }, [product.business_id, resource]);
  const matches = rows.filter(row => `${row.name} ${row.id}`.toLowerCase().includes(search.toLowerCase()));
  const save = async event => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      await api.post(`/${resource}/${product.id}/link-pos`, { source_id: source, reason, archive_duplicate: archive });
      onDone();
    } catch (error) {
      setError(formatApiError(error));
      setConflict(error.response?.data?.detail?.code === 'POS_SOURCE_ALREADY_LINKED');
    } finally { setBusy(false); }
  };
  return <Sheet open onOpenChange={open => { if (!open) onClose(); }}><SheetContent className="overflow-y-auto"><SheetHeader><SheetTitle>Review POS mapping: {product.name}</SheetTitle></SheetHeader>
    <p className="my-4 text-sm text-zinc-500">Choose the exact existing {noun} in this business. Names alone are never used to merge records. This changes the AdminCore mapping and retains the POS record.</p>
    <form onSubmit={save} className="space-y-4">
      <Input aria-label={`Search POS ${resource}`} placeholder="Search name or source ID" value={search} onChange={event => { setSearch(event.target.value); setSource(''); }} />
      <label className="block text-sm">POS {noun}<select className="mt-1 w-full rounded border p-2" required value={source} onChange={event => { setSource(event.target.value); setConflict(false); setArchive(false); }}><option value="">Select an exact source record</option>{matches.slice(0, 100).map(row => <option key={row.id} value={row.id}>{row.name} — {row.id}</option>)}</select></label>
      {matches.length > 100 && <p className="text-xs text-zinc-500">Showing 100 of {matches.length} matches. Narrow the search.</p>}
      <label className="block text-sm">Reason<Input required minLength={8} maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
      {conflict && <label className="flex gap-2 text-sm"><input type="checkbox" checked={archive} onChange={event => setArchive(event.target.checked)} />Archive this local duplicate and keep the existing source-linked record. Its history will be retained.</label>}
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      <Button disabled={busy || !source || reason.trim().length < 8 || (conflict && !archive)} type="submit">Confirm mapping</Button>
    </form>
  </SheetContent></Sheet>;
}
