import { useState, useEffect } from 'react';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Switch } from '@/components/ui/switch';
import { Checkbox } from '@/components/ui/checkbox';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { CreditCard, Plus, MoreHorizontal, Pencil, Trash2, Check, X, Users, MapPin, Blocks, Plug, Sparkles } from 'lucide-react';

const FEATURE_LABELS = {
  white_label: 'White Label',
  api_access: 'API Access',
  priority_support: 'Priority Support',
  custom_domain: 'Custom Domain',
  audit_logs: 'Audit Logs',
  advanced_analytics: 'Advanced Analytics',
};

const LIMIT_LABELS = {
  max_outlets: { label: 'Outlets', icon: MapPin },
  max_users: { label: 'Users', icon: Users },
  max_modules: { label: 'Modules', icon: Blocks },
  max_integrations: { label: 'Integrations', icon: Plug },
};

const DEFAULT_FORM = {
  name: '', slug: '', description: '', trial_days: 14,
  pricing: { monthly: 0, yearly: 0, currency: 'USD' },
  limits: { max_outlets: 1, max_users: 3, max_modules: 3, max_integrations: 0, max_products: 500, max_transactions_monthly: 1000 },
  included_modules: [],
  features: { white_label: false, api_access: false, priority_support: false, custom_domain: false, audit_logs: false, advanced_analytics: false },
  sort_order: 0,
};

export default function PlansPage() {
  const [plans, setPlans] = useState([]);
  const [modules, setModules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ ...DEFAULT_FORM });

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [plansRes, modsRes] = await Promise.all([api.get('/plans'), api.get('/modules')]);
        setPlans(plansRes.data);
        setModules(modsRes.data);
      } catch { toast.error('Failed to load plans'); }
      finally { setLoading(false); }
    };
    fetchData();
  }, []);

  const refresh = async () => {
    const { data } = await api.get('/plans');
    setPlans(data);
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ ...DEFAULT_FORM, pricing: { ...DEFAULT_FORM.pricing }, limits: { ...DEFAULT_FORM.limits }, features: { ...DEFAULT_FORM.features }, included_modules: [] });
    setSheetOpen(true);
  };

  const openEdit = (p) => {
    setEditing(p);
    setForm({
      name: p.name, slug: p.slug, description: p.description || '', trial_days: p.trial_days || 0,
      pricing: { ...DEFAULT_FORM.pricing, ...p.pricing },
      limits: { ...DEFAULT_FORM.limits, ...p.limits },
      included_modules: [...(p.included_modules || [])],
      features: { ...DEFAULT_FORM.features, ...p.features },
      sort_order: p.sort_order || 0,
    });
    setSheetOpen(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editing) {
        await api.put(`/plans/${editing.id}`, {
          name: form.name, description: form.description, trial_days: form.trial_days,
          pricing: form.pricing, limits: form.limits, included_modules: form.included_modules,
          features: form.features, sort_order: form.sort_order,
        });
        toast.success('Plan updated');
      } else {
        await api.post('/plans', form);
        toast.success('Plan created');
      }
      setSheetOpen(false);
      refresh();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const handleDelete = async (p) => {
    if (!window.confirm(`Delete plan "${p.name}"? Active subscriptions will be affected.`)) return;
    try {
      await api.delete(`/plans/${p.id}`);
      toast.success('Plan deleted');
      refresh();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const toggleModule = (slug) => {
    setForm(prev => ({
      ...prev,
      included_modules: prev.included_modules.includes(slug)
        ? prev.included_modules.filter(s => s !== slug)
        : [...prev.included_modules, slug],
    }));
  };

  const setLimit = (key, value) => setForm(prev => ({ ...prev, limits: { ...prev.limits, [key]: parseInt(value) || 0 } }));
  const setPrice = (key, value) => setForm(prev => ({ ...prev, pricing: { ...prev.pricing, [key]: parseFloat(value) || 0 } }));
  const setFeature = (key, value) => setForm(prev => ({ ...prev, features: { ...prev.features, [key]: value } }));

  const TIER_COLORS = { free: 'border-zinc-300', starter: 'border-blue-300', pro: 'border-violet-300', enterprise: 'border-amber-300' };

  return (
    <div className="space-y-6" data-testid="plans-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Plans</h1>
          <p className="text-sm text-zinc-500 mt-0.5">Define subscription tiers with limits, modules, and features</p>
        </div>
        <Button onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="add-plan-btn">
          <Plus className="h-4 w-4" /> Create Plan
        </Button>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[1,2,3,4].map(i => <div key={i} className="h-64 bg-zinc-200 rounded-lg animate-pulse" />)}
        </div>
      ) : plans.length === 0 ? (
        <div className="text-center py-16 text-zinc-400">
          <CreditCard className="h-10 w-10 mx-auto mb-3 opacity-40" />
          <p className="text-sm">No plans defined yet</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
          {plans.map(plan => (
            <Card key={plan.id} className={`shadow-sm hover:shadow-md transition-all relative ${TIER_COLORS[plan.slug] || 'border-zinc-200'}`} data-testid={`plan-card-${plan.slug}`}>
              {plan.slug === 'pro' && (
                <div className="absolute -top-2.5 left-1/2 -translate-x-1/2">
                  <Badge className="bg-violet-600 text-white text-[10px] shadow-sm">Popular</Badge>
                </div>
              )}
              <CardContent className="p-5">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <h3 className="text-base font-semibold text-zinc-900 font-heading">{plan.name}</h3>
                    <p className="text-xs text-zinc-500 mt-0.5">{plan.description}</p>
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-36">
                      <DropdownMenuItem onClick={() => openEdit(plan)}><Pencil className="h-3.5 w-3.5 mr-2" />Edit</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleDelete(plan)} className="text-red-600 focus:text-red-600"><Trash2 className="h-3.5 w-3.5 mr-2" />Delete</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>

                <div className="mb-4">
                  <span className="text-2xl font-bold text-zinc-900 font-heading">${plan.pricing?.monthly || 0}</span>
                  <span className="text-xs text-zinc-400">/mo</span>
                  {plan.pricing?.yearly > 0 && (
                    <span className="text-xs text-zinc-400 ml-2">(${plan.pricing.yearly}/yr)</span>
                  )}
                </div>

                {plan.trial_days > 0 && (
                  <Badge variant="outline" className="text-[10px] mb-3">{plan.trial_days}-day trial</Badge>
                )}

                <Separator className="my-3" />

                <div className="space-y-2">
                  {Object.entries(LIMIT_LABELS).map(([key, { label, icon: Icon }]) => (
                    <div key={key} className="flex items-center gap-2 text-xs">
                      <Icon className="h-3 w-3 text-zinc-400" />
                      <span className="text-zinc-600">{plan.limits?.[key] >= 999 ? 'Unlimited' : plan.limits?.[key] || 0} {label}</span>
                    </div>
                  ))}
                </div>

                <Separator className="my-3" />

                <div className="space-y-1.5">
                  <p className="text-[10px] font-semibold uppercase tracking-widest text-zinc-400 mb-1">Features</p>
                  {Object.entries(FEATURE_LABELS).map(([key, label]) => (
                    <div key={key} className="flex items-center gap-2 text-xs">
                      {plan.features?.[key]
                        ? <Check className="h-3 w-3 text-emerald-500" />
                        : <X className="h-3 w-3 text-zinc-300" />
                      }
                      <span className={plan.features?.[key] ? 'text-zinc-700' : 'text-zinc-400'}>{label}</span>
                    </div>
                  ))}
                </div>

                <div className="mt-3 pt-3 border-t border-zinc-100">
                  <p className="text-[10px] text-zinc-400">{plan.included_modules?.length || 0} modules included</p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="sm:max-w-lg">
          <SheetHeader><SheetTitle className="font-heading">{editing ? 'Edit Plan' : 'Create Plan'}</SheetTitle></SheetHeader>
          <ScrollArea className="h-[calc(100vh-8rem)] pr-4">
            <form onSubmit={handleSubmit} className="space-y-6 mt-4 pb-8">
              {/* Basic Info */}
              <div className="space-y-4">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Basic Info</p>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5"><Label className="text-xs">Name</Label><Input value={form.name} onChange={e => { setForm({...form, name: e.target.value, slug: editing ? form.slug : e.target.value.toLowerCase().replace(/\s+/g, '-')}); }} required data-testid="plan-name-input" /></div>
                  <div className="space-y-1.5"><Label className="text-xs">Slug</Label><Input value={form.slug} onChange={e => setForm({...form, slug: e.target.value})} required disabled={!!editing} data-testid="plan-slug-input" /></div>
                </div>
                <div className="space-y-1.5"><Label className="text-xs">Description</Label><Input value={form.description} onChange={e => setForm({...form, description: e.target.value})} data-testid="plan-desc-input" /></div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5"><Label className="text-xs">Trial Days</Label><Input type="number" min={0} value={form.trial_days} onChange={e => setForm({...form, trial_days: parseInt(e.target.value) || 0})} data-testid="plan-trial-input" /></div>
                  <div className="space-y-1.5"><Label className="text-xs">Sort Order</Label><Input type="number" value={form.sort_order} onChange={e => setForm({...form, sort_order: parseInt(e.target.value) || 0})} /></div>
                </div>
              </div>

              <Separator />

              {/* Pricing */}
              <div className="space-y-4">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Pricing</p>
                <div className="grid grid-cols-3 gap-3">
                  <div className="space-y-1.5"><Label className="text-xs">Monthly ($)</Label><Input type="number" min={0} step={0.01} value={form.pricing.monthly} onChange={e => setPrice('monthly', e.target.value)} data-testid="plan-price-monthly" /></div>
                  <div className="space-y-1.5"><Label className="text-xs">Yearly ($)</Label><Input type="number" min={0} step={0.01} value={form.pricing.yearly} onChange={e => setPrice('yearly', e.target.value)} data-testid="plan-price-yearly" /></div>
                  <div className="space-y-1.5"><Label className="text-xs">Currency</Label><Input value={form.pricing.currency} onChange={e => setPrice('currency', e.target.value)} /></div>
                </div>
              </div>

              <Separator />

              {/* Limits */}
              <div className="space-y-4">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Resource Limits</p>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5"><Label className="text-xs">Max Outlets</Label><Input type="number" min={0} value={form.limits.max_outlets} onChange={e => setLimit('max_outlets', e.target.value)} data-testid="plan-limit-outlets" /></div>
                  <div className="space-y-1.5"><Label className="text-xs">Max Users</Label><Input type="number" min={0} value={form.limits.max_users} onChange={e => setLimit('max_users', e.target.value)} data-testid="plan-limit-users" /></div>
                  <div className="space-y-1.5"><Label className="text-xs">Max Modules</Label><Input type="number" min={0} value={form.limits.max_modules} onChange={e => setLimit('max_modules', e.target.value)} data-testid="plan-limit-modules" /></div>
                  <div className="space-y-1.5"><Label className="text-xs">Max Integrations</Label><Input type="number" min={0} value={form.limits.max_integrations} onChange={e => setLimit('max_integrations', e.target.value)} data-testid="plan-limit-integrations" /></div>
                  <div className="space-y-1.5"><Label className="text-xs">Max Products</Label><Input type="number" min={0} value={form.limits.max_products} onChange={e => setLimit('max_products', e.target.value)} /></div>
                  <div className="space-y-1.5"><Label className="text-xs">Max Transactions/mo</Label><Input type="number" min={0} value={form.limits.max_transactions_monthly} onChange={e => setLimit('max_transactions_monthly', e.target.value)} /></div>
                </div>
              </div>

              <Separator />

              {/* Included Modules */}
              <div className="space-y-3">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Included Modules ({form.included_modules.length})</p>
                <div className="grid grid-cols-2 gap-2">
                  {modules.map(mod => (
                    <label key={mod.slug} className="flex items-center gap-2 p-2 rounded-md hover:bg-zinc-50 cursor-pointer text-sm">
                      <Checkbox
                        checked={form.included_modules.includes(mod.slug)}
                        onCheckedChange={() => toggleModule(mod.slug)}
                        data-testid={`plan-module-${mod.slug}`}
                      />
                      <span className="text-zinc-700">{mod.name}</span>
                    </label>
                  ))}
                </div>
              </div>

              <Separator />

              {/* Features */}
              <div className="space-y-3">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Features</p>
                <div className="space-y-2">
                  {Object.entries(FEATURE_LABELS).map(([key, label]) => (
                    <div key={key} className="flex items-center justify-between py-1.5">
                      <span className="text-sm text-zinc-700">{label}</span>
                      <Switch checked={form.features[key] || false} onCheckedChange={v => setFeature(key, v)} data-testid={`plan-feature-${key}`} />
                    </div>
                  ))}
                </div>
              </div>

              <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" data-testid="plan-submit-btn">
                {editing ? 'Update Plan' : 'Create Plan'}
              </Button>
            </form>
          </ScrollArea>
        </SheetContent>
      </Sheet>
    </div>
  );
}
