import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Building2, Boxes, Gauge, MapPin, Plus, MoreHorizontal, Package, Pencil, RefreshCw, Settings, Shield, Trash2 } from 'lucide-react';

const BUSINESS_TYPES = ['restaurant', 'cafe', 'retail', 'salon', 'pharmacy', 'supermarket', 'custom'];
const PLANS = ['starter', 'pro', 'enterprise'];

export default function BusinessesPage() {
  const navigate = useNavigate();
  const { selectBusiness, refreshBusinesses } = useBusiness();
  const [businesses, setBusinesses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [retryingId, setRetryingId] = useState('');
  const [retryBusiness, setRetryBusiness] = useState(null);
  const [retryForm, setRetryForm] = useState({ owner_name: '', owner_email: '', owner_password: '' });
  const [form, setForm] = useState({
    name: '',
    type: 'restaurant',
    plan: 'starter',
    owner_name: '',
    owner_email: '',
    owner_password: '',
  });

  const fetchBusinesses = async () => {
    try {
      const { data } = await api.get('/businesses');
      setBusinesses(data);
    } catch (err) { toast.error(`Failed to load businesses: ${formatApiError(err)}`); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchBusinesses(); }, []);

  const openCreate = () => {
    setEditing(null);
    setForm({
      name: '',
      type: 'restaurant',
      plan: 'starter',
      owner_name: '',
      owner_email: '',
      owner_password: '',
    });
    setSheetOpen(true);
  };
  const openEdit = (biz) => { setEditing(biz); setForm({ name: biz.name, type: biz.type, plan: biz.plan, owner_name: '', owner_email: '', owner_password: '' }); setSheetOpen(true); };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    try {
      if (editing) {
        await api.put(`/businesses/${editing.id}`, { name: form.name, type: form.type, plan: form.plan });
        toast.success('Business updated');
      } else {
        await api.post('/businesses', form);
        toast.success('Business created and provisioned to POS');
      }
      setSheetOpen(false);
      fetchBusinesses();
      refreshBusinesses();
    } catch (err) {
      toast.error(formatApiError(err));
      fetchBusinesses();
      refreshBusinesses();
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (biz) => {
    if (!window.confirm(`Delete "${biz.name}"? This will remove all related data.`)) return;
    try {
      await api.delete(`/businesses/${biz.id}`);
      toast.success('Business deleted');
      fetchBusinesses();
      refreshBusinesses();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const openOperation = (biz, path) => {
    selectBusiness?.(biz);
    navigate(path);
  };

  const statusColor = (s) => s === 'active' ? 'bg-emerald-100 text-emerald-700 border-emerald-200' : 'bg-zinc-100 text-zinc-600 border-zinc-200';
  const posStatusColor = (status) => {
    if (status === 'synced' || status === 'connected') return 'bg-emerald-100 text-emerald-700 border-emerald-200';
    if (status === 'pending') return 'bg-amber-100 text-amber-700 border-amber-200';
    if (status === 'failed') return 'bg-red-100 text-red-700 border-red-200';
    return 'bg-zinc-100 text-zinc-600 border-zinc-200';
  };

  const openRetryProvision = (biz) => {
    setRetryBusiness(biz);
    setRetryForm({ owner_name: biz.name || '', owner_email: biz.pos_owner_email || '', owner_password: '' });
  };

  const retryProvision = async (e) => {
    e.preventDefault();
    if (!retryBusiness) return;
    setRetryingId(retryBusiness.id);
    try {
      await api.post(`/businesses/${retryBusiness.id}/provision-pos`, retryForm);
      toast.success('Business provisioned to POS');
      setRetryBusiness(null);
      fetchBusinesses();
      refreshBusinesses();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setRetryingId('');
    }
  };

  return (
    <div className="space-y-6" data-testid="businesses-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Businesses</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Manage your tenant businesses</p>
        </div>
        <Button onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="add-business-btn">
          <Plus className="h-4 w-4" /> Add Business
        </Button>
      </div>

      <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
        <Table>
          <TableHeader>
            <TableRow className="bg-zinc-50 hover:bg-zinc-50">
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Name</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Type</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Plan</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Status</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">POS</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Created</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Operations</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={8} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
            ) : businesses.length === 0 ? (
              <TableRow><TableCell colSpan={8} className="text-center py-12 text-zinc-400">No businesses yet</TableCell></TableRow>
            ) : businesses.map(biz => (
              <TableRow key={biz.id} className="hover:bg-zinc-50/50" data-testid={`business-row-${biz.slug}`}>
                <TableCell className="font-medium text-zinc-900 py-3">
                  <div className="flex items-center gap-2.5">
                    <div className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: biz.branding?.primary_color || '#0055FF' }} />
                    {biz.name}
                  </div>
                </TableCell>
                <TableCell className="text-zinc-600 capitalize py-3">{biz.type}</TableCell>
                <TableCell className="py-3"><Badge variant="outline" className="text-[11px] capitalize">{biz.plan}</Badge></TableCell>
                <TableCell className="py-3"><Badge className={`text-[11px] ${statusColor(biz.status)}`}>{biz.status}</Badge></TableCell>
                <TableCell className="py-3">
                  <div className="space-y-1">
                    <Badge className={`text-[11px] ${posStatusColor(biz.pos_provisioning_status || (biz.pos_synced ? 'synced' : 'not_configured'))}`}>
                      {biz.pos_provisioning_status || (biz.pos_synced ? 'synced' : 'not configured')}
                    </Badge>
                    {biz.pos_provisioning_error && <p className="max-w-[220px] truncate text-[11px] text-red-600" title={biz.pos_provisioning_error}>{biz.pos_provisioning_error}</p>}
                  </div>
                </TableCell>
                <TableCell className="text-zinc-500 text-xs py-3">{new Date(biz.created_at).toLocaleDateString()}</TableCell>
                <TableCell className="py-3">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Button variant="outline" size="sm" className="h-8 gap-1.5 border-zinc-200" onClick={() => openOperation(biz, '/')}>
                      <Gauge className="h-3.5 w-3.5" />Overview
                    </Button>
                    <Button variant="outline" size="sm" className="h-8 gap-1.5 border-zinc-200" onClick={() => openOperation(biz, '/outlets')}>
                      <MapPin className="h-3.5 w-3.5" />Outlets
                    </Button>
                    <Button variant="outline" size="sm" className="h-8 gap-1.5 border-zinc-200" onClick={() => openOperation(biz, '/products')}>
                      <Package className="h-3.5 w-3.5" />Products
                    </Button>
                  </div>
                </TableCell>
                <TableCell className="py-3">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-7 w-7" data-testid={`business-actions-${biz.slug}`}><MoreHorizontal className="h-4 w-4" /></Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-44">
                      <DropdownMenuItem onClick={() => openOperation(biz, '/modules')}><Boxes className="h-3.5 w-3.5 mr-2" />Modules</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => openOperation(biz, '/users')}><Shield className="h-3.5 w-3.5 mr-2" />Users</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => openOperation(biz, '/settings')}><Settings className="h-3.5 w-3.5 mr-2" />Settings</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => openRetryProvision(biz)} disabled={retryingId === biz.id}><RefreshCw className="h-3.5 w-3.5 mr-2" />Retry POS Provision</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => openEdit(biz)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleDelete(biz)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />Delete</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="sm:max-w-md">
          <SheetHeader>
            <SheetTitle className="font-heading">{editing ? 'Edit Business' : 'New Business'}</SheetTitle>
          </SheetHeader>
          <form onSubmit={handleSubmit} className="space-y-5 mt-6">
            <div className="space-y-2">
              <Label>Business Name</Label>
              <Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="e.g. My Restaurant" required data-testid="business-name-input" />
            </div>
            <div className="space-y-2">
              <Label>Business Type</Label>
              <Select value={form.type} onValueChange={v => setForm({...form, type: v})}>
                <SelectTrigger data-testid="business-type-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {BUSINESS_TYPES.map(t => <SelectItem key={t} value={t} className="capitalize">{t}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Plan</Label>
              <Select value={form.plan} onValueChange={v => setForm({...form, plan: v})}>
                <SelectTrigger data-testid="business-plan-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PLANS.map(p => <SelectItem key={p} value={p} className="capitalize">{p}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            {!editing && (
              <div className="space-y-4 border-t border-zinc-200 pt-4">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-950">Business Owner Login</h3>
                  <p className="text-xs text-zinc-500 mt-1">This login is created in AdminCore and provisioned to the connected POS.</p>
                </div>
                <div className="space-y-2">
                  <Label>Owner Name</Label>
                  <Input value={form.owner_name} onChange={e => setForm({...form, owner_name: e.target.value})} placeholder="e.g. Kumar" required data-testid="business-owner-name-input" />
                </div>
                <div className="space-y-2">
                  <Label>Owner Email</Label>
                  <Input type="email" value={form.owner_email} onChange={e => setForm({...form, owner_email: e.target.value})} placeholder="owner@example.com" required data-testid="business-owner-email-input" />
                </div>
                <div className="space-y-2">
                  <Label>Owner Password</Label>
                  <Input type="password" value={form.owner_password} onChange={e => setForm({...form, owner_password: e.target.value})} placeholder="Minimum 6 characters" required minLength={6} data-testid="business-owner-password-input" />
                </div>
              </div>
            )}
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" disabled={submitting} data-testid="business-submit-btn">
              {submitting ? (editing ? 'Updating...' : 'Provisioning...') : (editing ? 'Update Business' : 'Create & Provision Business')}
            </Button>
          </form>
        </SheetContent>
      </Sheet>

      <Sheet open={!!retryBusiness} onOpenChange={(open) => !open && setRetryBusiness(null)}>
        <SheetContent className="sm:max-w-md">
          <SheetHeader>
            <SheetTitle className="font-heading">Retry POS Provision</SheetTitle>
          </SheetHeader>
          <form onSubmit={retryProvision} className="space-y-5 mt-6">
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              This will create or relink the POS business, Main Outlet, and owner login for {retryBusiness?.name}.
            </div>
            <div className="space-y-2">
              <Label>Owner Name</Label>
              <Input value={retryForm.owner_name} onChange={e => setRetryForm({...retryForm, owner_name: e.target.value})} required />
            </div>
            <div className="space-y-2">
              <Label>Owner Email</Label>
              <Input type="email" value={retryForm.owner_email} onChange={e => setRetryForm({...retryForm, owner_email: e.target.value})} required />
            </div>
            <div className="space-y-2">
              <Label>Owner Password</Label>
              <Input type="password" value={retryForm.owner_password} onChange={e => setRetryForm({...retryForm, owner_password: e.target.value})} required minLength={6} />
            </div>
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" disabled={retryingId === retryBusiness?.id}>
              {retryingId === retryBusiness?.id ? 'Provisioning...' : 'Provision to POS'}
            </Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
