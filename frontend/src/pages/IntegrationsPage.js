import { useState, useEffect } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Plug, Plus, MoreHorizontal, Pencil, Trash2, Building2, CreditCard, Mail, ShoppingBag, MessageSquare, Webhook } from 'lucide-react';

const TYPE_ICONS = { payment: CreditCard, email: Mail, ecommerce: ShoppingBag, messaging: MessageSquare, webhook: Webhook };
const TYPE_COLORS = { payment: 'text-emerald-600 bg-emerald-50', email: 'text-blue-600 bg-blue-50', ecommerce: 'text-violet-600 bg-violet-50', messaging: 'text-amber-600 bg-amber-50', webhook: 'text-rose-600 bg-rose-50' };
const STATUS_COLORS = { active: 'bg-emerald-100 text-emerald-700 border-emerald-200', inactive: 'bg-zinc-100 text-zinc-600 border-zinc-200', error: 'bg-red-100 text-red-700 border-red-200' };

export default function IntegrationsPage() {
  const { selectedBusiness } = useBusiness();
  const [integrations, setIntegrations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ slug: '', name: '', type: 'payment' });

  const fetchIntegrations = async () => {
    if (!selectedBusiness) { setLoading(false); return; }
    try {
      const { data } = await api.get(`/integrations/business/${selectedBusiness.id}`);
      setIntegrations(data);
    } catch { toast.error('Failed to load integrations'); }
    finally { setLoading(false); }
  };

  useEffect(() => { setLoading(true); fetchIntegrations(); }, [selectedBusiness]);

  if (!selectedBusiness) {
    return (
      <div className="text-center py-20" data-testid="integrations-page">
        <Building2 className="h-12 w-12 mx-auto text-zinc-300 mb-4" />
        <h2 className="text-lg font-medium text-zinc-900 font-heading">Select a Business</h2>
        <p className="text-sm text-zinc-500 mt-1">Choose a business to manage integrations</p>
      </div>
    );
  }

  const openCreate = () => { setEditing(null); setForm({ slug: '', name: '', type: 'payment' }); setSheetOpen(true); };
  const openEdit = (intg) => { setEditing(intg); setForm({ slug: intg.slug, name: intg.name, type: intg.type }); setSheetOpen(true); };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editing) {
        await api.put(`/integrations/${editing.id}`, { name: form.name, status: editing.status });
        toast.success('Integration updated');
      } else {
        await api.post(`/integrations/business/${selectedBusiness.id}`, form);
        toast.success('Integration added');
      }
      setSheetOpen(false);
      fetchIntegrations();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const toggleStatus = async (intg) => {
    const newStatus = intg.status === 'active' ? 'inactive' : 'active';
    try {
      await api.put(`/integrations/${intg.id}`, { status: newStatus });
      toast.success(`Integration ${newStatus}`);
      fetchIntegrations();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const handleDelete = async (intg) => {
    if (!window.confirm(`Remove "${intg.name}"?`)) return;
    try {
      await api.delete(`/integrations/${intg.id}`);
      toast.success('Integration removed');
      fetchIntegrations();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  return (
    <div className="space-y-6" data-testid="integrations-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Integrations</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Connected services for {selectedBusiness.name}</p>
        </div>
        <Button onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="add-integration-btn">
          <Plus className="h-4 w-4" /> Add Integration
        </Button>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1,2,3].map(i => <div key={i} className="h-32 bg-zinc-200 rounded-lg animate-pulse" />)}
        </div>
      ) : integrations.length === 0 ? (
        <div className="text-center py-16 text-zinc-400">
          <Plug className="h-10 w-10 mx-auto mb-3 opacity-40" />
          <p className="text-sm">No integrations configured yet</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {integrations.map(intg => {
            const Icon = TYPE_ICONS[intg.type] || Plug;
            const iconColor = TYPE_COLORS[intg.type] || 'text-zinc-600 bg-zinc-50';
            return (
              <Card key={intg.id} className="shadow-sm border-zinc-200 hover:shadow-md transition-shadow" data-testid={`integration-card-${intg.slug}`}>
                <CardContent className="p-5">
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${iconColor}`}>
                        <Icon className="h-5 w-5" />
                      </div>
                      <div>
                        <h3 className="text-sm font-medium text-zinc-900">{intg.name}</h3>
                        <Badge variant="outline" className="text-[10px] mt-0.5">{intg.type}</Badge>
                      </div>
                    </div>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-40">
                        <DropdownMenuItem onClick={() => toggleStatus(intg)}>{intg.status === 'active' ? 'Deactivate' : 'Activate'}</DropdownMenuItem>
                        <DropdownMenuItem onClick={() => openEdit(intg)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                        <DropdownMenuItem onClick={() => handleDelete(intg)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />Remove</DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge className={`text-[11px] ${STATUS_COLORS[intg.status] || STATUS_COLORS.inactive}`}>{intg.status}</Badge>
                    {intg.webhook_url && <span className="text-[10px] text-zinc-400 font-mono truncate">{intg.webhook_url}</span>}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="sm:max-w-md">
          <SheetHeader><SheetTitle className="font-heading">{editing ? 'Edit Integration' : 'Add Integration'}</SheetTitle></SheetHeader>
          <form onSubmit={handleSubmit} className="space-y-5 mt-6">
            {!editing && (
              <div className="space-y-2"><Label>Slug</Label><Input value={form.slug} onChange={e => setForm({...form, slug: e.target.value.toLowerCase().replace(/\s+/g, '-')})} placeholder="e.g. stripe" required data-testid="integration-slug-input" /></div>
            )}
            <div className="space-y-2"><Label>Name</Label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="e.g. Stripe Payments" required data-testid="integration-name-input" /></div>
            <div className="space-y-2">
              <Label>Type</Label>
              <Select value={form.type} onValueChange={v => setForm({...form, type: v})}>
                <SelectTrigger data-testid="integration-type-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {['payment', 'email', 'ecommerce', 'messaging', 'webhook'].map(t => <SelectItem key={t} value={t} className="capitalize">{t}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" data-testid="integration-submit-btn">{editing ? 'Update' : 'Add Integration'}</Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
