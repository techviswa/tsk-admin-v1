import { useState, useEffect } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Textarea } from '@/components/ui/textarea';
import { Flag, Plus, MoreHorizontal, Pencil, Trash2, Building2 } from 'lucide-react';

export default function FeatureFlagsPage() {
  const { selectedBusiness } = useBusiness();
  const [flags, setFlags] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ key: '', name: '', description: '', enabled: false });

  const fetchFlags = async () => {
    if (!selectedBusiness) { setLoading(false); return; }
    try {
      const { data } = await api.get(`/feature-flags/business/${selectedBusiness.id}`);
      setFlags(data);
    } catch { toast.error('Failed to load feature flags'); }
    finally { setLoading(false); }
  };

  useEffect(() => { setLoading(true); fetchFlags(); }, [selectedBusiness]);

  if (!selectedBusiness) {
    return (
      <div className="text-center py-20" data-testid="feature-flags-page">
        <Building2 className="h-12 w-12 mx-auto text-zinc-300 mb-4" />
        <h2 className="text-lg font-medium text-zinc-900 font-heading">Select a Business</h2>
        <p className="text-sm text-zinc-500 mt-1">Choose a business to manage feature flags</p>
      </div>
    );
  }

  const openCreate = () => { setEditing(null); setForm({ key: '', name: '', description: '', enabled: false }); setSheetOpen(true); };
  const openEdit = (f) => { setEditing(f); setForm({ key: f.key, name: f.name, description: f.description || '', enabled: f.enabled }); setSheetOpen(true); };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editing) {
        await api.put(`/feature-flags/${editing.id}`, { name: form.name, description: form.description, enabled: form.enabled });
        toast.success('Flag updated');
      } else {
        await api.post(`/feature-flags/business/${selectedBusiness.id}`, form);
        toast.success('Flag created');
      }
      setSheetOpen(false);
      fetchFlags();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const toggleFlag = async (f) => {
    try {
      await api.put(`/feature-flags/${f.id}`, { enabled: !f.enabled });
      toast.success(`${f.name} ${!f.enabled ? 'enabled' : 'disabled'}`);
      fetchFlags();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const handleDelete = async (f) => {
    if (!window.confirm(`Delete flag "${f.name}"?`)) return;
    try {
      await api.delete(`/feature-flags/${f.id}`);
      toast.success('Flag deleted');
      fetchFlags();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  return (
    <div className="space-y-6" data-testid="feature-flags-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Feature Flags</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Toggle features for {selectedBusiness.name}</p>
        </div>
        <Button onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="add-flag-btn">
          <Plus className="h-4 w-4" /> Add Flag
        </Button>
      </div>

      <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
        <Table>
          <TableHeader>
            <TableRow className="bg-zinc-50 hover:bg-zinc-50">
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Name</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Key</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Description</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Status</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={5} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
            ) : flags.length === 0 ? (
              <TableRow><TableCell colSpan={5} className="text-center py-12 text-zinc-400">No feature flags</TableCell></TableRow>
            ) : flags.map(f => (
              <TableRow key={f.id} className="hover:bg-zinc-50/50">
                <TableCell className="font-medium text-zinc-900 py-3">
                  <div className="flex items-center gap-2"><Flag className="h-3.5 w-3.5 text-zinc-400" />{f.name}</div>
                </TableCell>
                <TableCell className="py-3"><code className="text-xs bg-zinc-100 px-1.5 py-0.5 rounded font-mono text-zinc-700">{f.key}</code></TableCell>
                <TableCell className="text-zinc-500 text-sm py-3 max-w-[200px] truncate">{f.description || '-'}</TableCell>
                <TableCell className="py-3">
                  <Switch checked={f.enabled} onCheckedChange={() => toggleFlag(f)} data-testid={`flag-toggle-${f.key}`} />
                </TableCell>
                <TableCell className="py-3">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-36">
                      <DropdownMenuItem onClick={() => openEdit(f)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleDelete(f)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />Delete</DropdownMenuItem>
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
          <SheetHeader><SheetTitle className="font-heading">{editing ? 'Edit Flag' : 'New Feature Flag'}</SheetTitle></SheetHeader>
          <form onSubmit={handleSubmit} className="space-y-5 mt-6">
            {!editing && <div className="space-y-2"><Label>Key</Label><Input value={form.key} onChange={e => setForm({...form, key: e.target.value.toLowerCase().replace(/\s+/g, '_')})} placeholder="e.g. enable_dark_mode" required data-testid="flag-key-input" /></div>}
            <div className="space-y-2"><Label>Display Name</Label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} required data-testid="flag-name-input" /></div>
            <div className="space-y-2"><Label>Description</Label><Textarea value={form.description} onChange={e => setForm({...form, description: e.target.value})} rows={3} data-testid="flag-desc-input" /></div>
            <div className="flex items-center justify-between py-2">
              <Label>Enabled by default</Label>
              <Switch checked={form.enabled} onCheckedChange={v => setForm({...form, enabled: v})} data-testid="flag-enabled-toggle" />
            </div>
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" data-testid="flag-submit-btn">{editing ? 'Update Flag' : 'Create Flag'}</Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
