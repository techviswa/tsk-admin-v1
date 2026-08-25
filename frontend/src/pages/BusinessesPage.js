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
import { Building2, Boxes, Gauge, MapPin, Plus, MoreHorizontal, Package, Pencil, Settings, Shield, Trash2 } from 'lucide-react';

const BUSINESS_TYPES = ['restaurant', 'cafe', 'retail', 'salon', 'pharmacy', 'supermarket', 'custom'];
const PLANS = ['starter', 'pro', 'enterprise'];

export default function BusinessesPage() {
  const navigate = useNavigate();
  const { selectBusiness, refreshBusinesses } = useBusiness();
  const [businesses, setBusinesses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({
    name: '',
    type: 'restaurant',
    plan: 'starter',
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
    });
    setSheetOpen(true);
  };
  const openEdit = (biz) => { setEditing(biz); setForm({ name: biz.name, type: biz.type, plan: biz.plan }); setSheetOpen(true); };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editing) {
        await api.put(`/businesses/${editing.id}`, form);
        toast.success('Business updated');
      } else {
        await api.post('/businesses', form);
        toast.success('Business created');
      }
      setSheetOpen(false);
      fetchBusinesses();
      refreshBusinesses();
    } catch (err) { toast.error(formatApiError(err)); }
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
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Created</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Operations</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={7} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
            ) : businesses.length === 0 ? (
              <TableRow><TableCell colSpan={7} className="text-center py-12 text-zinc-400">No businesses yet</TableCell></TableRow>
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
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" data-testid="business-submit-btn">
              {editing ? 'Update Business' : 'Create Business'}
            </Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
