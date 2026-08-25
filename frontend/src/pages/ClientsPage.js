import { useEffect, useState } from 'react';
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
import { Mail, MoreHorizontal, Pencil, Phone, Plus, Trash2, Users } from 'lucide-react';

const STATUSES = ['active', 'trial', 'suspended', 'inactive'];
const STATUS_COLORS = {
  active: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  trial: 'bg-blue-50 text-blue-700 border-blue-100',
  suspended: 'bg-amber-50 text-amber-700 border-amber-100',
  inactive: 'bg-zinc-100 text-zinc-600 border-zinc-200',
};

const emptyForm = { owner_name: '', email: '', phone: '', status: 'active', business_ids: [], notes: '' };

export default function ClientsPage() {
  const { businesses, selectedBusiness } = useBusiness();
  const [clients, setClients] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm);

  const fetchClients = async () => {
    setLoading(true);
    try {
      const params = {
        business_id: selectedBusiness?.id || undefined,
        status: status !== 'all' ? status : undefined,
        search: search || undefined,
      };
      const { data } = await api.get('/clients', { params });
      setClients(data);
    } catch (err) {
      toast.error(`Failed to load clients: ${formatApiError(err)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchClients(); }, [selectedBusiness, status]);

  const openCreate = () => {
    setEditing(null);
    setForm({ ...emptyForm, business_ids: selectedBusiness ? [selectedBusiness.id] : [] });
    setSheetOpen(true);
  };

  const openEdit = (client) => {
    setEditing(client);
    setForm({
      owner_name: client.owner_name || '',
      email: client.email || '',
      phone: client.phone || '',
      status: client.status || 'active',
      business_ids: client.business_ids || [],
      notes: client.notes || '',
    });
    setSheetOpen(true);
  };

  const toggleBusiness = (businessId) => {
    setForm(current => ({
      ...current,
      business_ids: current.business_ids.includes(businessId)
        ? current.business_ids.filter(id => id !== businessId)
        : [...current.business_ids, businessId],
    }));
  };

  const saveClient = async (event) => {
    event.preventDefault();
    try {
      if (editing) {
        await api.put(`/clients/${editing.id}`, form);
        toast.success('Client updated');
      } else {
        await api.post('/clients', form);
        toast.success('Client created');
      }
      setSheetOpen(false);
      fetchClients();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  const deleteClient = async (client) => {
    if (!window.confirm(`Delete client "${client.owner_name}"?`)) return;
    try {
      await api.delete(`/clients/${client.id}`);
      toast.success('Client deleted');
      fetchClients();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  return (
    <div className="space-y-6" data-testid="clients-page">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Clients</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            {selectedBusiness ? `Client owners assigned to ${selectedBusiness.name}` : 'Manage SaaS client owners and business assignments'}
          </p>
        </div>
        <Button onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 gap-2 self-start lg:self-auto" data-testid="add-client-btn">
          <Plus className="h-4 w-4" /> Add Client
        </Button>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row">
        <Input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === 'Enter' && fetchClients()} placeholder="Search name, email, phone" className="sm:max-w-xs" />
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="sm:w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {STATUSES.map(item => <SelectItem key={item} value={item} className="capitalize">{item}</SelectItem>)}
          </SelectContent>
        </Select>
        <Button variant="outline" onClick={fetchClients}>Search</Button>
      </div>

      <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
        <Table>
          <TableHeader>
            <TableRow className="bg-zinc-50 hover:bg-zinc-50">
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Client Owner</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Contact</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Assigned Businesses</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Status</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={5} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
            ) : clients.length === 0 ? (
              <TableRow><TableCell colSpan={5} className="text-center py-12 text-zinc-400">No clients found</TableCell></TableRow>
            ) : clients.map(client => (
              <TableRow key={client.id} className="hover:bg-zinc-50/50">
                <TableCell className="py-3">
                  <div className="font-medium text-zinc-900">{client.owner_name}</div>
                  {client.notes && <div className="text-xs text-zinc-500 mt-0.5 truncate max-w-xs">{client.notes}</div>}
                </TableCell>
                <TableCell className="py-3 text-sm text-zinc-600">
                  <div className="flex items-center gap-1.5"><Mail className="h-3.5 w-3.5 text-zinc-400" />{client.email || '-'}</div>
                  <div className="flex items-center gap-1.5 mt-1"><Phone className="h-3.5 w-3.5 text-zinc-400" />{client.phone || '-'}</div>
                </TableCell>
                <TableCell className="py-3">
                  <div className="flex flex-wrap gap-1.5">
                    {(client.assigned_businesses || []).length === 0 ? (
                      <span className="text-sm text-zinc-400">None</span>
                    ) : client.assigned_businesses.map(biz => (
                      <Badge key={biz.id} variant="outline" className="text-[11px]">{biz.name}</Badge>
                    ))}
                  </div>
                </TableCell>
                <TableCell className="py-3">
                  <Badge className={`text-[11px] capitalize ${STATUS_COLORS[client.status] || STATUS_COLORS.inactive}`}>{client.status}</Badge>
                </TableCell>
                <TableCell className="py-3">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-36">
                      <DropdownMenuItem onClick={() => openEdit(client)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => deleteClient(client)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />Delete</DropdownMenuItem>
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
          <SheetHeader><SheetTitle className="font-heading">{editing ? 'Edit Client' : 'New Client'}</SheetTitle></SheetHeader>
          <form onSubmit={saveClient} className="space-y-5 mt-6">
            <div className="space-y-2"><Label>Owner Name</Label><Input value={form.owner_name} onChange={e => setForm({...form, owner_name: e.target.value})} required /></div>
            <div className="space-y-2"><Label>Email</Label><Input type="email" value={form.email} onChange={e => setForm({...form, email: e.target.value})} /></div>
            <div className="space-y-2"><Label>Phone</Label><Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} /></div>
            <div className="space-y-2">
              <Label>Status</Label>
              <Select value={form.status} onValueChange={value => setForm({...form, status: value})}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{STATUSES.map(item => <SelectItem key={item} value={item} className="capitalize">{item}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Assigned Businesses</Label>
              <div className="max-h-44 overflow-auto rounded-md border border-zinc-200 p-2 space-y-1">
                {(businesses || []).map(biz => (
                  <button key={biz.id} type="button" onClick={() => toggleBusiness(biz.id)} className={`w-full text-left px-2 py-2 rounded text-sm ${form.business_ids.includes(biz.id) ? 'bg-blue-50 text-blue-700' : 'hover:bg-zinc-50 text-zinc-700'}`}>
                    <span className="inline-flex items-center gap-2"><Users className="h-3.5 w-3.5" />{biz.name}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-2"><Label>Notes</Label><Input value={form.notes} onChange={e => setForm({...form, notes: e.target.value})} placeholder="Account notes" /></div>
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700">{editing ? 'Update Client' : 'Create Client'}</Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
