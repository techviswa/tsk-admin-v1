import { useState, useEffect } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import { useAuth } from '@/contexts/AuthContext';
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
import { Eye, EyeOff, Shield, Plus, MoreHorizontal, Pencil, RefreshCw, Trash2 } from 'lucide-react';

const ROLES = ['platform_admin', 'business_owner', 'manager', 'staff', 'support_admin'];
const STATUSES = ['active', 'inactive'];
const ROLE_COLORS = {
  platform_admin: 'bg-red-50 text-red-700 border-red-100',
  business_owner: 'bg-blue-50 text-blue-700 border-blue-100',
  manager: 'bg-violet-50 text-violet-700 border-violet-100',
  staff: 'bg-zinc-100 text-zinc-700 border-zinc-200',
  support_admin: 'bg-amber-50 text-amber-700 border-amber-100',
};
const STATUS_COLORS = {
  active: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  inactive: 'bg-zinc-100 text-zinc-600 border-zinc-200',
};

export default function UsersPage() {
  const { selectedBusiness, businesses } = useBusiness();
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', email: '', password: '', role: 'staff', status: 'active', business_ids: [] });
  const [showPassword, setShowPassword] = useState(false);

  const fetchUsers = async () => {
    try {
      const params = selectedBusiness ? { business_id: selectedBusiness.id } : {};
      const { data } = await api.get('/users', { params });
      const rows = selectedBusiness ? data.filter(u => u.role !== 'platform_admin') : data;
      setUsers(rows);
    } catch (err) { toast.error(`Failed to load users: ${formatApiError(err)}`); }
    finally { setLoading(false); }
  };

  useEffect(() => { setLoading(true); fetchUsers(); }, [selectedBusiness]);

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', email: '', password: '', role: 'staff', status: 'active', business_ids: selectedBusiness ? [selectedBusiness.id] : [] });
    setShowPassword(false);
    setSheetOpen(true);
  };
  const openEdit = (u) => {
    setEditing(u);
    setForm({ name: u.name, email: u.email, password: '', role: u.role, status: u.status || 'active', business_ids: u.business_ids || [] });
    setShowPassword(false);
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

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      const scopedBusinessIds = selectedBusiness ? [selectedBusiness.id] : form.business_ids;
      if (editing) {
        const updateData = { name: form.name, email: form.email, role: form.role, status: form.status, business_ids: scopedBusinessIds };
        if (form.password) updateData.password = form.password;
        await api.put(`/users/${editing.id}`, updateData);
        toast.success('User updated');
      } else {
        const createData = { ...form, business_ids: scopedBusinessIds };
        delete createData.status;
        await api.post('/users', createData);
        toast.success('User created');
      }
      setSheetOpen(false);
      fetchUsers();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const handleDelete = async (u) => {
    if (!window.confirm(`Delete user "${u.name}"?`)) return;
    try {
      await api.delete(`/users/${u.id}`);
      toast.success('User deleted');
      fetchUsers();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const handleSyncToPOS = async (u) => {
    try {
      await api.post(`/users/${u.id}/sync-pos`);
      toast.success('User synced to POS');
      fetchUsers();
    } catch (err) { toast.error(`POS sync failed: ${formatApiError(err)}`); }
  };

  return (
    <div className="space-y-6" data-testid="users-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Users & Roles</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            {selectedBusiness ? `Business team for ${selectedBusiness.name}` : 'Platform users and business staff'}
          </p>
        </div>
        <Button onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="add-user-btn">
          <Plus className="h-4 w-4" /> Add User
        </Button>
      </div>

      <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
        <Table>
          <TableHeader>
            <TableRow className="bg-zinc-50 hover:bg-zinc-50">
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Name</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Email</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Role</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Status</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={5} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
            ) : users.length === 0 ? (
              <TableRow><TableCell colSpan={5} className="text-center py-12 text-zinc-400">No users found</TableCell></TableRow>
            ) : users.map(u => (
              <TableRow key={u.id} className="hover:bg-zinc-50/50">
                <TableCell className="font-medium text-zinc-900 py-3">
                  <div className="flex items-center gap-2.5">
                    <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center shrink-0">
                      <span className="text-[10px] font-semibold text-white">{u.name?.[0]?.toUpperCase()}</span>
                    </div>
                    {u.name}
                  </div>
                </TableCell>
                <TableCell className="text-zinc-600 py-3 text-xs font-mono">{u.email}</TableCell>
                <TableCell className="py-3">
                  <Badge className={`text-[11px] ${ROLE_COLORS[u.role] || 'bg-zinc-100 text-zinc-600'}`}>
                    {u.role?.replace(/_/g, ' ')}
                  </Badge>
                </TableCell>
                <TableCell className="py-3">
                  <Badge className={`text-[11px] ${STATUS_COLORS[u.status] || STATUS_COLORS.inactive}`}>
                    {u.status}
                  </Badge>
                </TableCell>
                <TableCell className="py-3">
                  {u.id !== currentUser?.id && (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-36">
                        <DropdownMenuItem onClick={() => openEdit(u)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                        <DropdownMenuItem onClick={() => handleSyncToPOS(u)}><RefreshCw className="h-3.5 w-3.5 mr-2" />Sync to POS</DropdownMenuItem>
                        <DropdownMenuItem onClick={() => handleDelete(u)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />Delete</DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="sm:max-w-md">
          <SheetHeader><SheetTitle className="font-heading">{editing ? 'Edit User' : 'New User'}</SheetTitle></SheetHeader>
          <form onSubmit={handleSubmit} className="space-y-5 mt-6">
            <div className="space-y-2"><Label>Full Name</Label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} required data-testid="user-name-input" /></div>
            <div className="space-y-2"><Label>Email</Label><Input type="email" value={form.email} onChange={e => setForm({...form, email: e.target.value})} required data-testid="user-email-input" /></div>
            <div className="space-y-2">
              <Label>{editing ? 'Reset Password' : 'Password'}</Label>
              <div className="relative">
                <Input type={showPassword ? 'text' : 'password'} value={form.password} onChange={e => setForm({...form, password: e.target.value})} required={!editing} placeholder={editing ? 'Enter a new password only if changing it' : ''} data-testid="user-password-input" className="pr-10" />
                <Button type="button" variant="ghost" size="icon" className="absolute right-1 top-1 h-8 w-8 text-zinc-500 hover:text-zinc-900" onClick={() => setShowPassword(value => !value)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
              </div>
              {editing && <p className="text-xs text-zinc-500">Current passwords are encrypted and cannot be viewed. Set a new password here if needed.</p>}
            </div>
            <div className="space-y-2">
              <Label>Role</Label>
              <Select value={form.role} onValueChange={v => setForm({...form, role: v})}>
                <SelectTrigger data-testid="user-role-select"><SelectValue /></SelectTrigger>
                <SelectContent>{ROLES.map(r => <SelectItem key={r} value={r}>{r.replace(/_/g, ' ')}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            {editing && (
              <div className="space-y-2">
                <Label>Status</Label>
                <Select value={form.status} onValueChange={v => setForm({...form, status: v})}>
                  <SelectTrigger data-testid="user-status-select"><SelectValue /></SelectTrigger>
                  <SelectContent>{STATUSES.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            )}
            {selectedBusiness ? (
              <div className="space-y-2">
                <Label>Business Access</Label>
                <div className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-700">
                  {selectedBusiness.name}
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <Label>Business Access</Label>
                <div className="max-h-44 overflow-auto rounded-md border border-zinc-200 p-2 space-y-1">
                  {(businesses || []).map(biz => (
                    <button
                      key={biz.id}
                      type="button"
                      onClick={() => toggleBusiness(biz.id)}
                      className={`w-full text-left px-2 py-2 rounded text-sm ${form.business_ids.includes(biz.id) ? 'bg-blue-50 text-blue-700' : 'hover:bg-zinc-50 text-zinc-700'}`}
                    >
                      {biz.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" data-testid="user-submit-btn">{editing ? 'Update User' : 'Create User'}</Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
