import { useState, useEffect, useCallback, useRef } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import { useAuth } from '@/contexts/AuthContext';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { assignableRoles, PLATFORM_ROLE_LABELS } from '@/lib/permissions';
import { profileUpdateBlocked, profileUpdateMessage } from '@/lib/profileUpdateStatus';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Eye, EyeOff, Shield, Plus, MoreHorizontal, Pencil, RefreshCw, Trash2 } from 'lucide-react';

const STATUSES = ['active', 'inactive'];
const ROLE_COLORS = {
  platform_admin: 'bg-red-50 text-red-700 border-red-100',
  business_owner: 'bg-blue-50 text-blue-700 border-blue-100',
  manager: 'bg-violet-50 text-violet-700 border-violet-100',
  staff: 'bg-zinc-100 text-zinc-700 border-zinc-200',
  support_admin: 'bg-amber-50 text-amber-700 border-amber-100',
  platform_operations: 'bg-orange-50 text-orange-700 border-orange-100',
  platform_finance: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  platform_security: 'bg-rose-50 text-rose-700 border-rose-100',
  platform_auditor: 'bg-sky-50 text-sky-700 border-sky-100',
};
const roleLabel = (role) => PLATFORM_ROLE_LABELS[role] || String(role || '').replace(/_/g, ' ');
const STATUS_COLORS = {
  active: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  inactive: 'bg-zinc-100 text-zinc-600 border-zinc-200',
};

export default function UsersPage() {
  const { selectedBusiness, selectBusiness, businesses, businessesLoaded } = useBusiness();
  const { user: currentUser } = useAuth();
  const member = currentUser?.pos_links?.[selectedBusiness?.id];
  const actor = PLATFORM_ROLE_LABELS[currentUser?.role] ? currentUser : { ...currentUser,
    role: ['inactive', 'disabled'].includes(member?.status) ? 'staff' : (member?.role === 'owner' ? 'business_owner' : member?.role) || (currentUser?.id && selectedBusiness?.owner_id === currentUser.id ? 'business_owner' : currentUser?.role) };
  const roleInBusiness = row => {
    const role = row.pos_links?.[selectedBusiness?.id]?.role || row.role;
    return role === 'owner' ? 'business_owner' : role;
  };
  const statusInBusiness = row => row.pos_links?.[selectedBusiness?.id]?.status || row.status;
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', email: '', password: '', role: 'staff', status: 'active', business_ids: [] });
  const [showPassword, setShowPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const submissionInFlight = useRef(false);
  const previousUpdateStatuses = useRef(new Map());
  const activeBusinessId = useRef(selectedBusiness?.id);
  activeBusinessId.current = selectedBusiness?.id;
  const editingUser = users.find(user => user.id === editing?.id) || editing;
  const editBlocked = profileUpdateBlocked(editingUser);

  const fetchUsers = useCallback(async () => {
    const businessId = selectedBusiness?.id;
    if (!businessId) return;
    try {
      const params = { business_id: businessId };
      const { data } = await api.get('/users', { params });
      if (activeBusinessId.current !== businessId) return;
      const rows = data.filter(u => !PLATFORM_ROLE_LABELS[u.role]);
      rows.forEach(user => {
        const previous = previousUpdateStatuses.current.get(user.id);
        if (previous && previous !== 'synced' && user.pos_update_status === 'synced') {
          toast.success(`${user.name || 'User'}: profile update synchronized with POS`);
        }
        previousUpdateStatuses.current.set(user.id, user.pos_update_status);
      });
      setUsers(rows);
    } catch (err) {
      if (activeBusinessId.current === businessId) toast.error(`Failed to load users: ${formatApiError(err)}`);
    }
    finally { if (activeBusinessId.current === businessId) setLoading(false); }
  }, [selectedBusiness]);

  useEffect(() => {
    setUsers([]);
    setSheetOpen(false);
    setEditing(null);
    previousUpdateStatuses.current.clear();
    setLoading(Boolean(selectedBusiness));
    fetchUsers();
  }, [fetchUsers, selectedBusiness]);
  const hasPendingUpdates = users.some(u => ['pending', 'running', 'retrying'].includes(u.pos_update_status));
  useEffect(() => {
    if (!selectedBusiness || !hasPendingUpdates) return undefined;
    const timer = setInterval(fetchUsers, 10000);
    return () => clearInterval(timer);
  }, [fetchUsers, hasPendingUpdates, selectedBusiness]);

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

  const missingBusinessIds = businessesLoaded
    ? (editing?.business_ids || []).filter(id => !businesses.some(business => business.id === id))
    : [];

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
    if (!selectedBusiness || submissionInFlight.current || editBlocked) return;
    submissionInFlight.current = true;
    setSaving(true);
    try {
      const scopedBusinessIds = selectedBusiness ? (editing?.business_ids || [selectedBusiness.id]) : form.business_ids;
      if (editing) {
        const updateData = { name: form.name, email: form.email, role: form.role, status: form.status, business_ids: scopedBusinessIds };
        if (form.password) updateData.password = form.password;
        const { data } = await api.put(`/users/${editing.id}`, updateData);
        setUsers(current => current.map(user => user.id === editing.id ? { ...user, ...data } : user));
        if (data.pos_update_status === 'pending') toast.info('Update queued. Credentials change after POS synchronization completes.');
        else toast.success('User updated');
      } else {
        const createData = { ...form, business_ids: scopedBusinessIds };
        delete createData.status;
        const { data } = await api.post('/users', createData);
        if (data.pos_update_status === 'pending') toast.info('User created; POS access is pending synchronization.');
        else toast.success('User created');
      }
      setSheetOpen(false);
      await fetchUsers();
    } catch (err) {
      if (err.response?.status === 409) await fetchUsers();
      toast.error(formatApiError(err));
    } finally {
      submissionInFlight.current = false;
      setSaving(false);
    }
  };

  const handleDelete = async (u) => {
    if (!window.confirm(`${u.pos_synced || Object.keys(u.pos_links || {}).length ? 'Deactivate' : 'Delete'} user "${u.name}"? Connected POS accounts retain their history.`)) return;
    try {
      const { data } = await api.delete(`/users/${u.id}`);
      toast.success(data.message || 'User deleted');
      fetchUsers();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const handleResetMfa = async (u) => {
    const reason = window.prompt(`Reset two-factor authentication for ${u.email}? Enter a reason (recorded in the audit log):`);
    if (!reason) return;
    try {
      await api.post(`/users/${u.id}/mfa/reset`, { reason });
      toast.success('Two-factor authentication reset; the user was signed out');
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const handleSyncToPOS = async (u) => {
    try {
      const { data } = await api.post(`/users/${u.id}/sync-pos`);
      toast.info(data.status === 'running' ? 'POS sync is in progress' : 'POS sync queued. Credentials are not confirmed until sync completes.');
      await fetchUsers();
    } catch (err) { toast.error(`POS sync failed: ${formatApiError(err)}`); }
  };

  if (!selectedBusiness) {
    return (
      <div className="space-y-6" data-testid="business-teams-page">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Business teams</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Select a business to manage its users and roles.</p>
        </div>
        <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
          <Table>
            <TableHeader><TableRow>
              <TableHead>Business</TableHead><TableHead>Type</TableHead><TableHead>Status</TableHead><TableHead />
            </TableRow></TableHeader>
            <TableBody>
              {!businessesLoaded ? <TableRow><TableCell colSpan={4}>Loading businesses...</TableCell></TableRow>
                : businesses.length === 0 ? <TableRow><TableCell colSpan={4}>No businesses found</TableCell></TableRow>
                : businesses.map(business => <TableRow key={business.id}>
                  <TableCell className="font-medium">{business.name}</TableCell>
                  <TableCell className="capitalize">{business.type}</TableCell>
                  <TableCell className="capitalize">{business.status}</TableCell>
                  <TableCell className="text-right"><Button variant="outline" size="sm" onClick={() => selectBusiness(business)}>View team</Button></TableCell>
                </TableRow>)}
            </TableBody>
          </Table>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="users-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Users & Roles</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            {`Business team for ${selectedBusiness.name}`}
          </p>
        </div>
        <Button onClick={openCreate} disabled={!assignableRoles(actor).length} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="add-user-btn">
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
                    {u.can_manage_account === false && <p className="text-xs font-normal text-zinc-500">Account changes require authority across all assigned businesses.</p>}
                  </div>
                </TableCell>
                <TableCell className="text-zinc-600 py-3 text-xs font-mono">{u.email}</TableCell>
                <TableCell className="py-3">
                  <Badge className={`text-[11px] ${ROLE_COLORS[roleInBusiness(u)] || 'bg-zinc-100 text-zinc-600'}`}>
                    {roleLabel(roleInBusiness(u))}
                  </Badge>
                </TableCell>
                <TableCell className="py-3">
                  <Badge className={`text-[11px] ${STATUS_COLORS[statusInBusiness(u)] || STATUS_COLORS.inactive}`}>
                    {statusInBusiness(u)}
                  </Badge>
                  {u.pos_update_status && u.pos_update_status !== 'synced' && (
                    <div className="mt-1 max-w-sm text-xs text-amber-700" title={u.pos_update_error || (u.pos_update_retry_at ? `Next attempt: ${new Date(u.pos_update_retry_at).toLocaleString()}` : undefined)}>
                      {profileUpdateMessage(u)}
                    </div>
                  )}
                </TableCell>
                <TableCell className="py-3">
                  {u.id !== currentUser?.id && u.can_manage_account !== false && (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-36">
                        <DropdownMenuItem onClick={() => openEdit(u)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                        <DropdownMenuItem disabled={['pending', 'running', 'retrying'].includes(u.pos_update_status)} onClick={() => handleSyncToPOS(u)}><RefreshCw className="h-3.5 w-3.5 mr-2" />{u.pos_update_status === 'failed' ? 'Retry failed update' : 'Sync to POS'}</DropdownMenuItem>
                        {currentUser?.role === 'platform_admin' && <DropdownMenuItem onClick={() => handleResetMfa(u)}><Shield className="h-3.5 w-3.5 mr-2" />Reset 2FA</DropdownMenuItem>}
                        <DropdownMenuItem onClick={() => handleDelete(u)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />{u.pos_synced || Object.keys(u.pos_links || {}).length ? 'Deactivate' : 'Delete'}</DropdownMenuItem>
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
            {(editBlocked || editingUser?.pos_update_status === 'rejected') && (
              <div role="status" className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">
                <p>{profileUpdateMessage(editingUser)}</p>
                {editingUser.pos_update_status === 'failed' && <Button type="button" variant="outline" className="mt-2" onClick={() => handleSyncToPOS(editingUser)}>Retry previous update</Button>}
              </div>
            )}
            <fieldset disabled={saving || editBlocked} className="space-y-5">
            {missingBusinessIds.length > 0 && (
              <p role="status" className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">
                This account references {missingBusinessIds.length} deleted businesses. Saving as a platform admin removes obsolete assignments. Existing POS links require repair before saving.
              </p>
            )}
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
                <SelectContent>{[...new Set([...assignableRoles(actor).filter(role => !PLATFORM_ROLE_LABELS[role]), ...(editing ? [editing.role] : [])])].map(r => <SelectItem key={r} value={r}>{roleLabel(r)}</SelectItem>)}</SelectContent>
              </Select>
              {editing && (editing.business_ids?.length || 0) > 1 && <p className="text-xs text-zinc-500">Profile, role, status and credential changes here apply across this account’s assigned businesses. Business-specific permissions remain managed in POS.</p>}
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
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" data-testid="user-submit-btn">{saving ? 'Submitting...' : editBlocked ? 'Waiting for previous update' : editing ? 'Update User' : 'Create User'}</Button>
            </fieldset>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
