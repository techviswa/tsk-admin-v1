import { useState, useEffect } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import { useAuth } from '@/contexts/AuthContext';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import {
  BadgeDollarSign,
  Banknote,
  BarChart3,
  Bell,
  Blocks,
  Building2,
  CalendarDays,
  ChefHat,
  CreditCard,
  FileUp,
  FileWarning,
  Grid3X3,
  Handshake,
  Heart,
  Layers3,
  Package,
  PackageOpen,
  Plug,
  Printer,
  QrCode,
  Receipt,
  ReceiptText,
  Search,
  ShieldCheck,
  ShoppingCart,
  Store,
  TicketPercent,
  ToggleLeft,
  Truck,
  UserCog,
  Users,
  Wallet,
} from 'lucide-react';

const ICON_MAP = {
  BadgeDollarSign,
  Banknote,
  BarChart3,
  Bell,
  Blocks,
  Building2,
  CalendarDays,
  ChefHat,
  CreditCard,
  FileUp,
  FileWarning,
  Grid3X3,
  Handshake,
  Heart,
  Layers3,
  Package,
  PackageOpen,
  Plug,
  Printer,
  QrCode,
  Receipt,
  ReceiptText,
  ShieldCheck,
  ShoppingCart,
  Store,
  TicketPercent,
  ToggleLeft,
  Truck,
  UserCog,
  Users,
  Wallet,
};

const CATEGORY_COLORS = {
  saas: 'bg-indigo-50 text-indigo-700 border-indigo-100',
  pos: 'bg-blue-50 text-blue-700 border-blue-100',
  operations: 'bg-blue-50 text-blue-700 border-blue-100',
  finance: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  intelligence: 'bg-violet-50 text-violet-700 border-violet-100',
  engagement: 'bg-amber-50 text-amber-700 border-amber-100',
  hr: 'bg-rose-50 text-rose-700 border-rose-100',
  management: 'bg-sky-50 text-sky-700 border-sky-100',
};

const CATEGORY_LABELS = {
  saas: 'SaaS Control',
  pos: 'POS Operations',
  operations: 'Operations',
  finance: 'Finance',
  intelligence: 'Reports',
  engagement: 'Customer & QR',
  hr: 'Staff',
  management: 'Management',
};

export default function ModulesPage() {
  const { selectedBusiness, refreshBusinessModules } = useBusiness();
  const { user } = useAuth();
  const [modules, setModules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  const fetchModules = async () => {
    if (!selectedBusiness) { setLoading(false); return; }
    try {
      const { data } = await api.get(`/modules/business/${selectedBusiness.id}`);
      setModules(data);
    } catch { toast.error('Failed to load modules'); }
    finally { setLoading(false); }
  };

  useEffect(() => { setLoading(true); fetchModules(); }, [selectedBusiness]);

  const toggleModule = async (mod) => {
    const outsidePlan = mod.outside_plan || !mod.included;
    const canOverride = user?.role === 'platform_admin';
    if (!mod.enabled && outsidePlan && !canOverride) {
      toast.error(`${mod.name} is outside this business plan. A platform admin override is required.`);
      return;
    }
    try {
      await api.put(`/modules/business/${selectedBusiness.id}/${mod.slug}`, {
        enabled: !mod.enabled,
        config: {},
        override_reason: outsidePlan ? 'Platform admin module override from Modules UI' : '',
      });
      toast.success(`${mod.name} ${!mod.enabled ? 'enabled' : 'disabled'}`);
      await fetchModules();
      if (refreshBusinessModules) await refreshBusinessModules();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const filteredModules = modules.filter(mod => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return [mod.name, mod.slug, mod.category, mod.description].some(value => String(value || '').toLowerCase().includes(q));
  });
  const enabledCount = modules.filter(mod => mod.enabled).length;
  const coreCount = modules.filter(mod => mod.is_core).length;
  const groupedModules = filteredModules.reduce((groups, mod) => {
    const key = mod.category || 'other';
    groups[key] = groups[key] || [];
    groups[key].push(mod);
    return groups;
  }, {});
  const categoryOrder = ['saas', 'pos', 'operations', 'finance', 'intelligence', 'engagement', 'hr', 'management', 'other'];

  if (!selectedBusiness) {
    return (
      <div className="text-center py-20" data-testid="modules-page">
        <Building2 className="h-12 w-12 mx-auto text-zinc-300 mb-4" />
        <h2 className="text-lg font-medium text-zinc-900 font-heading">Select a Business</h2>
        <p className="text-sm text-zinc-500 mt-1">Choose a business to manage modules</p>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="modules-page">
      <div>
        <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Modules</h1>
        <p className="text-sm text-zinc-500 mt-0.5">Control SaaS and POS capabilities for {selectedBusiness.name}</p>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1,2,3,4,5,6].map(i => <div key={i} className="h-32 bg-zinc-200 rounded-lg animate-pulse" />)}
        </div>
      ) : (
        <div className="space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Card><CardContent className="p-4"><p className="text-[11px] uppercase tracking-widest text-zinc-400 font-semibold">Total Modules</p><p className="text-2xl font-semibold text-zinc-950 mt-1">{modules.length}</p></CardContent></Card>
            <Card><CardContent className="p-4"><p className="text-[11px] uppercase tracking-widest text-zinc-400 font-semibold">Enabled</p><p className="text-2xl font-semibold text-zinc-950 mt-1">{enabledCount}</p></CardContent></Card>
            <Card><CardContent className="p-4"><p className="text-[11px] uppercase tracking-widest text-zinc-400 font-semibold">Core</p><p className="text-2xl font-semibold text-zinc-950 mt-1">{coreCount}</p></CardContent></Card>
          </div>

          <div className="relative max-w-xl">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-400" />
            <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search modules..." className="pl-9" />
          </div>

          {categoryOrder.filter(category => groupedModules[category]?.length).map(category => (
            <section key={category} className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-zinc-900">{CATEGORY_LABELS[category] || category}</h2>
                <Badge variant="outline" className="text-[10px]">{groupedModules[category].length} modules</Badge>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {groupedModules[category].map(mod => {
                  const Icon = ICON_MAP[mod.icon] || Package;
                  const outsidePlan = mod.outside_plan || !mod.included;
                  const canOverride = user?.role === 'platform_admin';
                  const lockedByPlan = !mod.enabled && outsidePlan && !canOverride;
                  return (
                    <Card key={mod.slug} className={`shadow-sm transition-all duration-200 ${mod.enabled ? 'border-zinc-200 hover:border-blue-200' : 'border-zinc-200 opacity-70 hover:opacity-100'}`} data-testid={`module-card-${mod.slug}`}>
                      <CardContent className="p-5">
                        <div className="flex items-start justify-between mb-3">
                          <div className="flex items-center gap-3 min-w-0">
                            <div className={`w-9 h-9 rounded-lg flex shrink-0 items-center justify-center ${mod.enabled ? 'bg-blue-50 text-blue-600' : 'bg-zinc-100 text-zinc-400'}`}>
                              <Icon className="h-4.5 w-4.5" />
                            </div>
                            <div className="min-w-0">
                              <h3 className="text-sm font-medium text-zinc-900 truncate">{mod.name}</h3>
                              <Badge className={`text-[10px] mt-0.5 ${CATEGORY_COLORS[mod.category] || 'bg-zinc-50 text-zinc-600'}`}>{CATEGORY_LABELS[mod.category] || mod.category}</Badge>
                            </div>
                          </div>
                          <Switch
                            checked={mod.enabled}
                            disabled={lockedByPlan}
                            onCheckedChange={() => toggleModule(mod)}
                            data-testid={`module-toggle-${mod.slug}`}
                            title={lockedByPlan ? 'Outside current plan. Platform admin override required.' : ''}
                          />
                        </div>
                        <p className="text-xs text-zinc-500 leading-relaxed min-h-[48px]">{mod.description}</p>
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          {mod.is_core && <Badge variant="outline" className="text-[10px]">Core</Badge>}
                          {mod.included ? <Badge className="text-[10px] bg-emerald-50 text-emerald-700 border-emerald-100">Included</Badge> : <Badge className="text-[10px] bg-amber-50 text-amber-700 border-amber-100">Outside Plan</Badge>}
                          {mod.override && <Badge className="text-[10px] bg-violet-50 text-violet-700 border-violet-100">Override</Badge>}
                          {lockedByPlan && <Badge className="text-[10px] bg-red-50 text-red-700 border-red-100">Locked</Badge>}
                          <Badge variant="outline" className="text-[10px] font-mono">{mod.slug}</Badge>
                        </div>
                        {lockedByPlan && (
                          <p className="mt-2 text-[11px] leading-4 text-red-600">Upgrade the plan or ask a platform admin to override this module.</p>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
