import { useState, useEffect, useCallback, useRef } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { useAuth } from '@/contexts/AuthContext';
import ProductSourceReview from '@/components/ProductSourceReview';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { MapPin, Plus, MoreHorizontal, Pencil, Trash2, Building2 } from 'lucide-react';

export default function OutletsPage() {
  const { selectedBusiness } = useBusiness();
  const { user } = useAuth();
  const [review, setReview] = useState(null);
  const [status, setStatus] = useState('active');
  const businessId = selectedBusiness?.id;
  const setupRequired = selectedBusiness?.pos_connection_state === 'setup_required';
  const [snapshot, setSnapshot] = useState(null);
  const [paging, setPaging] = useState({ businessId: null, offset: 0 });
  const offset = paging.businessId === businessId ? paging.offset : 0;
  const hasSnapshot = Boolean(businessId && snapshot && snapshot.businessId === businessId && snapshot.offset === offset);
  const outlets = hasSnapshot ? snapshot.rows : [];
  const total = hasSnapshot ? snapshot.total : 0;
  const requestVersion = useRef(0);
  const activeBusiness = useRef(businessId);
  activeBusiness.current = businessId;
  const activeOffset = useRef(offset);
  activeOffset.current = offset;
  const submitting = useRef(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', code: '', address: '', manager_name: '', phone: '', status: 'active' });

  const fetchOutlets = useCallback(async () => {
    if (businessId !== activeBusiness.current || offset !== activeOffset.current) return;
    const version = ++requestVersion.current;
    if (!businessId) { setLoading(false); return; }
    try {
      const { data, headers } = await api.get(`/outlets/business/${businessId}`, { params: { limit: 50, offset, status } });
      if (version === requestVersion.current) setSnapshot({ businessId, offset, rows: data, total: Number(headers?.['x-total-count'] ?? data.length) });
    } catch (error) {
      if (version === requestVersion.current) toast.error(formatApiError(error));
    } finally { if (version === requestVersion.current) setLoading(false); }
  }, [businessId, offset, status]);

  useEffect(() => {
    setLoading(true); fetchOutlets();
    return () => { requestVersion.current += 1; };
  }, [fetchOutlets]);
  useEffect(() => { setSheetOpen(false); setEditing(null); setReview(null); }, [businessId]);
  const mappingMissing = outlet => Boolean(selectedBusiness?.pos_external_id && !outlet.pos_external_id && !outlet.pos_command?.version);
  const versionMissing = outlet => Boolean(outlet.pos_external_id && !outlet.pos_metadata?.updated_at);
  const blocked = outlet => setupRequired || outlet.pos_sync_pending || mappingMissing(outlet) || versionMissing(outlet);
  const pending = outlets.some(outlet => (outlet.pos_sync_pending && outlet.pos_command_status !== 'failed') || (selectedBusiness?.pos_external_id && versionMissing(outlet)));
  useEffect(() => {
    if (!businessId) return undefined;
    const timer = setInterval(fetchOutlets, pending ? 3000 : 30000);
    return () => clearInterval(timer);
  }, [businessId, fetchOutlets, pending]);

  if (!selectedBusiness) {
    return (
      <div className="text-center py-20" data-testid="outlets-page">
        <Building2 className="h-12 w-12 mx-auto text-zinc-300 mb-4" />
        <h2 className="text-lg font-medium text-zinc-900 font-heading">Select a Business</h2>
        <p className="text-sm text-zinc-500 mt-1">Choose a business from the top navigation to manage outlets</p>
      </div>
    );
  }

  const openCreate = () => { setEditing(null); setForm({ request_id: crypto.randomUUID(), name: '', code: '', address: '', manager_name: '', phone: '', status: 'active' }); setSheetOpen(true); };
  const openEdit = (o) => {
    setEditing(o);
    setForm({
      expected_updated_at: o.pos_metadata?.updated_at || undefined,
      name: o.name || '',
      code: o.code || '',
      address: o.address || '',
      manager_name: o.manager_name || '',
      phone: o.phone || '',
      status: o.status || 'active',
    });
    setSheetOpen(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (submitting.current) return;
    submitting.current = true; setSaving(true);
    const targetBusiness = businessId;
    try {
      const result = editing ? await api.put(`/outlets/${editing.id}`, form) : await api.post(`/outlets/business/${targetBusiness}`, form);
      if (activeBusiness.current !== targetBusiness) return;
      if (result.status === 202) toast.info('Outlet change queued. It takes effect after POS confirms it.');
      else toast.success(editing ? 'Outlet updated' : 'Outlet created');
      setSheetOpen(false); fetchOutlets();
    } catch (err) { if (activeBusiness.current === targetBusiness) toast.error(formatApiError(err)); }
    finally { submitting.current = false; setSaving(false); }
  };

  const handleDelete = async (o) => {
    if (!window.confirm(`Delete "${o.name}"?`)) return;
    try {
      const result = await api.delete(`/outlets/${o.id}`, { params: { expected_updated_at: o.pos_metadata?.updated_at } });
      if (activeBusiness.current !== businessId) return;
      if (result.status === 202) toast.info('Deletion queued. The outlet stays unchanged until POS confirms it; historical records are retained.');
      else toast.success('Outlet deleted');
      fetchOutlets();
    } catch (err) { if (activeBusiness.current === businessId) toast.error(formatApiError(err)); }
  };

  const toggleStatus = async (o) => {
    if (blocked(o)) return;
    const newStatus = o.status === 'active' ? 'inactive' : 'active';
    try {
      const result = await api.put(`/outlets/${o.id}`, { status: newStatus, expected_updated_at: o.pos_metadata?.updated_at });
      if (activeBusiness.current !== businessId) return;
      if (result.status === 202) toast.info('Status change queued; waiting for POS confirmation.');
      else toast.success(`Outlet ${newStatus}`);
      fetchOutlets();
    } catch (err) { if (activeBusiness.current === businessId) toast.error(formatApiError(err)); }
  };

  return (
    <div className="space-y-6" data-testid="outlets-page">
      <div className="flex flex-wrap gap-3 items-center justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Outlets</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Branches and locations for {selectedBusiness.name}</p>
        </div>
        <div className="flex gap-2">
        {selectedBusiness.pos_external_id && <Button disabled={setupRequired} variant="outline" onClick={async () => { try { await api.post('/pos-bridge/queue/outlets', null, { params: { business_id: businessId } }); if (activeBusiness.current === businessId) toast.info('POS refresh queued. Saved outlets remain visible until the import completes.'); fetchOutlets(); } catch (error) { if (activeBusiness.current === businessId) toast.error(formatApiError(error)); } }}>Refresh from POS</Button>}
        <Button disabled={setupRequired} onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="add-outlet-btn">
          <Plus className="h-4 w-4" /> Add Outlet
        </Button>
        </div>
      </div>

      <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
        <div className="p-3 border-b flex flex-wrap items-center gap-3">
          <label htmlFor="outlet-status" className="text-sm">Outlet status</label>
          <select id="outlet-status" className="border rounded px-3 py-2 text-sm" value={status} onChange={event => { requestVersion.current += 1; setSnapshot(null); setPaging({ businessId, offset: 0 }); setStatus(event.target.value); }}>
            <option value="active">Active</option><option value="inactive">Inactive</option><option value="archived">Archived</option><option value="all">All statuses</option>
          </select>
          <p className="text-xs text-zinc-500">Inactive outlets remain available for administration and historical records.</p>
        </div>
        <Table>
          <TableHeader>
            <TableRow className="bg-zinc-50 hover:bg-zinc-50">
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Name</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Code</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Location</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Manager</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Phone</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Status</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={7} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
            ) : outlets.length === 0 ? (
              <TableRow><TableCell colSpan={7} className="text-center py-12 text-zinc-400">{status === 'all' ? 'No outlets yet' : `No ${status} outlets. Choose All statuses to review other outlets.`}</TableCell></TableRow>
            ) : outlets.map(o => (
              <TableRow key={o.id} className="hover:bg-zinc-50/50">
                <TableCell className="font-medium text-zinc-900 py-3">
                  <div className="flex items-center gap-2"><MapPin className="h-3.5 w-3.5 text-zinc-400" />{o.name}</div>
                  {o.pos_sync_pending && <Badge variant="outline" className="mt-1">{o.pos_command_status === 'failed' ? 'Sync failed' : 'Waiting for POS'}</Badge>}
                  {versionMissing(o) && !o.pos_sync_pending && <p className="text-xs text-amber-700 mt-1">Refresh from POS before editing this older record.</p>}
                  {mappingMissing(o) && <p className="text-xs text-amber-700 mt-1">Source mapping needs review.</p>}
                  {mappingMissing(o) && user?.role === 'platform_admin' && <Button disabled={setupRequired} variant="outline" size="sm" onClick={() => setReview(o)}>Review mapping</Button>}
                  {o.pos_sync_error?.message && <p className="text-xs text-red-600 mt-1">{o.pos_sync_error.message}</p>}
                  {o.pos_sync_pending && o.pos_command_status === 'failed' && <Button variant="outline" size="sm" className="mt-1" onClick={async () => { try { await api.post(`/outlets/${o.id}/retry-pos`); fetchOutlets(); } catch (error) { if (activeBusiness.current === businessId) toast.error(formatApiError(error)); } }}>Retry sync</Button>}
                </TableCell>
                <TableCell className="text-zinc-600 py-3 font-mono text-xs">{o.code || '-'}</TableCell>
                <TableCell className="text-zinc-600 py-3">{o.address || 'Not provided'}</TableCell>
                <TableCell className="text-zinc-600 py-3">{o.manager_name || 'Not assigned'}</TableCell>
                <TableCell className="text-zinc-600 py-3 font-mono text-xs">{o.phone || 'Not provided'}</TableCell>
                <TableCell className="py-3">
                  <Badge role="button" aria-disabled={blocked(o) || undefined} className={`text-[11px] cursor-pointer ${o.status === 'active' ? 'bg-emerald-100 text-emerald-700 border-emerald-200' : 'bg-zinc-100 text-zinc-600 border-zinc-200'}`} onClick={() => toggleStatus(o)}>
                    {o.status}
                  </Badge>
                </TableCell>
                <TableCell className="py-3">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-36">
                      <DropdownMenuItem disabled={blocked(o)} onClick={() => openEdit(o)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                      <DropdownMenuItem disabled={blocked(o)} onClick={() => handleDelete(o)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />Delete</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-between text-sm text-zinc-500">
        <span>{total} {status === 'all' ? '' : `${status} `}{total === 1 ? 'outlet' : 'outlets'}</span><div className="flex gap-2">
          <Button variant="outline" disabled={!offset || loading} onClick={() => setPaging({ businessId, offset: Math.max(0, offset - 50) })}>Previous</Button>
          <Button variant="outline" disabled={offset + 50 >= total || loading} onClick={() => setPaging({ businessId, offset: offset + 50 })}>Next</Button>
        </div>
      </div>
      {review && <ProductSourceReview resource="outlets" product={review} onClose={() => setReview(null)} onDone={() => { setReview(null); fetchOutlets(); }} />}
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="sm:max-w-md">
          <SheetHeader><SheetTitle className="font-heading">{editing ? 'Edit Outlet' : 'New Outlet'}</SheetTitle></SheetHeader>
          <form onSubmit={handleSubmit} className="space-y-5 mt-6">
            <div className="space-y-2"><Label>Outlet Name</Label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} required data-testid="outlet-name-input" /></div>
            <div className="space-y-2"><Label>Outlet Code</Label><Input value={form.code} onChange={e => setForm({...form, code: e.target.value})} placeholder="Auto-generated if blank" data-testid="outlet-code-input" /></div>
            <div className="space-y-2"><Label>Location</Label><Input value={form.address} onChange={e => setForm({...form, address: e.target.value})} data-testid="outlet-address-input" /></div>
            <div className="space-y-2"><Label>Manager Name</Label><Input value={form.manager_name} onChange={e => setForm({...form, manager_name: e.target.value})} data-testid="outlet-manager-input" /></div>
            <div className="space-y-2"><Label>Phone</Label><Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} data-testid="outlet-phone-input" /></div>
            <div className="space-y-2">
              <Label>Status</Label>
              <Select value={form.status} onValueChange={value => setForm({...form, status: value})}>
                <SelectTrigger data-testid="outlet-status-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">active</SelectItem>
                  <SelectItem value="inactive">inactive</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button type="submit" disabled={saving} className="w-full bg-blue-600 hover:bg-blue-700" data-testid="outlet-submit-btn">{editing ? 'Update Outlet' : 'Create Outlet'}</Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
