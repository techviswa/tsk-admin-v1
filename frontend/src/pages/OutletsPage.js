import { useState, useEffect } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
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
  const [outlets, setOutlets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', code: '', address: '', manager_name: '', phone: '', status: 'active' });

  const fetchOutlets = async () => {
    if (!selectedBusiness) { setLoading(false); return; }
    try {
      const { data } = await api.get(`/outlets/business/${selectedBusiness.id}`);
      setOutlets(data);
    } catch { toast.error('Failed to load outlets'); }
    finally { setLoading(false); }
  };

  useEffect(() => { setLoading(true); fetchOutlets(); }, [selectedBusiness]);

  if (!selectedBusiness) {
    return (
      <div className="text-center py-20" data-testid="outlets-page">
        <Building2 className="h-12 w-12 mx-auto text-zinc-300 mb-4" />
        <h2 className="text-lg font-medium text-zinc-900 font-heading">Select a Business</h2>
        <p className="text-sm text-zinc-500 mt-1">Choose a business from the top navigation to manage outlets</p>
      </div>
    );
  }

  const openCreate = () => { setEditing(null); setForm({ name: 'Main Outlet', code: '', address: '', manager_name: '', phone: '', status: 'active' }); setSheetOpen(true); };
  const openEdit = (o) => {
    setEditing(o);
    setForm({
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
    try {
      if (editing) {
        await api.put(`/outlets/${editing.id}`, form);
        toast.success('Outlet updated');
      } else {
        await api.post(`/outlets/business/${selectedBusiness.id}`, form);
        toast.success('Outlet created');
      }
      setSheetOpen(false);
      fetchOutlets();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const handleDelete = async (o) => {
    if (!window.confirm(`Delete "${o.name}"?`)) return;
    try {
      await api.delete(`/outlets/${o.id}`);
      toast.success('Outlet deleted');
      fetchOutlets();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const toggleStatus = async (o) => {
    const newStatus = o.status === 'active' ? 'inactive' : 'active';
    try {
      await api.put(`/outlets/${o.id}`, { status: newStatus });
      toast.success(`Outlet ${newStatus}`);
      fetchOutlets();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  return (
    <div className="space-y-6" data-testid="outlets-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Outlets</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Branches and locations for {selectedBusiness.name}</p>
        </div>
        <Button onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="add-outlet-btn">
          <Plus className="h-4 w-4" /> Add Outlet
        </Button>
      </div>

      <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
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
              <TableRow><TableCell colSpan={7} className="text-center py-12 text-zinc-400">No outlets yet</TableCell></TableRow>
            ) : outlets.map(o => (
              <TableRow key={o.id} className="hover:bg-zinc-50/50">
                <TableCell className="font-medium text-zinc-900 py-3">
                  <div className="flex items-center gap-2"><MapPin className="h-3.5 w-3.5 text-zinc-400" />{o.name}</div>
                </TableCell>
                <TableCell className="text-zinc-600 py-3 font-mono text-xs">{o.code || '-'}</TableCell>
                <TableCell className="text-zinc-600 py-3">{o.address}</TableCell>
                <TableCell className="text-zinc-600 py-3">{o.manager_name || '-'}</TableCell>
                <TableCell className="text-zinc-600 py-3 font-mono text-xs">{o.phone}</TableCell>
                <TableCell className="py-3">
                  <Badge className={`text-[11px] cursor-pointer ${o.status === 'active' ? 'bg-emerald-100 text-emerald-700 border-emerald-200' : 'bg-zinc-100 text-zinc-600 border-zinc-200'}`} onClick={() => toggleStatus(o)}>
                    {o.status}
                  </Badge>
                </TableCell>
                <TableCell className="py-3">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-36">
                      <DropdownMenuItem onClick={() => openEdit(o)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleDelete(o)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />Delete</DropdownMenuItem>
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
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" data-testid="outlet-submit-btn">{editing ? 'Update Outlet' : 'Create Outlet'}</Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
