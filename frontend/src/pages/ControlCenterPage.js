import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Activity, ArrowRight, Blocks, Building2, CreditCard, Flag, Plug, Receipt,
  RefreshCw, Shield, Store, Users
} from 'lucide-react';
import { POS_ADMIN_NAV } from '@/lib/posAdminNav';

const SAAS_ICONS = {
  businesses: Building2,
  users: Users,
  roles: Shield,
  modules: Blocks,
  plans: CreditCard,
  subscriptions: Receipt,
  billing: CreditCard,
  'feature-flags': Flag,
  'audit-logs': Activity,
  integrations: Plug,
};

const SUMMARY_CARDS = [
  { key: 'businesses', label: 'Businesses', icon: Building2, color: 'text-blue-600 bg-blue-50' },
  { key: 'users', label: 'Users', icon: Users, color: 'text-amber-600 bg-amber-50' },
  { key: 'active_subscriptions', label: 'Active Subscriptions', icon: Receipt, color: 'text-emerald-600 bg-emerald-50' },
  { key: 'monthly_recurring_revenue', label: 'MRR', icon: CreditCard, color: 'text-violet-600 bg-violet-50', money: true },
  { key: 'saas_modules', label: 'SaaS Controls', icon: Blocks, color: 'text-sky-600 bg-sky-50' },
  { key: 'pos_modules', label: 'POS Controls', icon: Store, color: 'text-rose-600 bg-rose-50' },
  { key: 'pos_records', label: 'POS Records', icon: Activity, color: 'text-teal-600 bg-teal-50' },
];

function money(value) {
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(Number(value || 0));
}

function ControlCard({ module, icon: Icon }) {
  return (
    <Card className="shadow-sm border-zinc-200">
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <div className="h-8 w-8 rounded-lg bg-zinc-100 text-zinc-700 flex items-center justify-center shrink-0">
                <Icon className="h-4 w-4" />
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium text-zinc-900 truncate">{module.label}</p>
                <p className="text-xs text-zinc-400">
                  {module.priority ? `Priority ${module.priority} - ` : ''}{module.count ?? 0} records
                </p>
              </div>
            </div>
          </div>
          <Link to={module.path}>
            <Button variant="outline" size="sm" className="h-8 gap-1.5 border-zinc-200">
              Open <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

export default function ControlCenterPage() {
  const { selectedBusiness } = useBusiness();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchOverview = useCallback(async () => {
    setLoading(true);
    try {
      const params = selectedBusiness ? { business_id: selectedBusiness.id } : {};
      const { data: result } = await api.get('/control-center/overview', { params });
      setData(result);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, [selectedBusiness]);

  useEffect(() => { fetchOverview(); }, [fetchOverview]);

  const posIconByPath = Object.fromEntries(POS_ADMIN_NAV.map(item => [item.path, item.icon]));

  if (loading && !data) {
    return (
      <div className="space-y-4">
        <div className="h-10 w-72 bg-zinc-200 rounded animate-pulse" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[1, 2, 3, 4, 5, 6].map(i => <div key={i} className="h-24 bg-zinc-200 rounded-lg animate-pulse" />)}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="control-center-page">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">SaaS + POS Control Center</h1>
          <p className="text-sm text-zinc-500 mt-1">
            {selectedBusiness ? `${selectedBusiness.name} control surface` : 'Platform-wide control surface for SaaS and POS operations'}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchOverview} disabled={loading} className="gap-1.5 self-start lg:self-auto">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />Refresh
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {SUMMARY_CARDS.map(({ key, label, icon: Icon, color, money: isMoney }) => (
          <Card key={key} className="shadow-sm border-zinc-200">
            <CardContent className="p-5">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">{label}</p>
                  <p className="text-2xl font-semibold text-zinc-900 mt-1 font-heading truncate">
                    {isMoney ? money(data?.summary?.[key]) : data?.summary?.[key] ?? 0}
                  </p>
                </div>
                <div className={`h-10 w-10 rounded-lg flex items-center justify-center shrink-0 ${color}`}>
                  <Icon className="h-5 w-5" />
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <Card className="shadow-sm border-zinc-200">
          <CardHeader className="px-5 py-4 border-b border-zinc-100">
            <CardTitle className="text-base font-medium text-zinc-900">SaaS-Side Control</CardTitle>
          </CardHeader>
          <CardContent className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
            {(data?.saas_modules || []).map(module => {
              const Icon = SAAS_ICONS[module.key] || Blocks;
              return <ControlCard key={module.key} module={module} icon={Icon} />;
            })}
          </CardContent>
        </Card>

        <Card className="shadow-sm border-zinc-200">
          <CardHeader className="px-5 py-4 border-b border-zinc-100">
            <CardTitle className="text-base font-medium text-zinc-900">POS-Side Control</CardTitle>
          </CardHeader>
          <CardContent className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
            {(data?.pos_modules || []).map(module => {
              const Icon = posIconByPath[module.path] || Store;
              return <ControlCard key={module.key} module={module} icon={Icon} />;
            })}
          </CardContent>
        </Card>
      </div>

      <Card className="shadow-sm border-zinc-200">
        <CardHeader className="px-5 py-4 border-b border-zinc-100 flex flex-row items-center justify-between">
          <CardTitle className="text-base font-medium text-zinc-900">Recent Control Activity</CardTitle>
          <Badge variant="outline" className="text-[11px]">{data?.scope || 'platform'}</Badge>
        </CardHeader>
        <CardContent className="p-0">
          {(data?.recent_activity || []).length === 0 ? (
            <div className="px-5 py-10 text-center text-sm text-zinc-400">No recent activity</div>
          ) : (
            <div className="divide-y divide-zinc-100">
              {data.recent_activity.map((log, index) => (
                <div key={log.id || index} className="px-5 py-3 flex items-center justify-between gap-3 text-sm">
                  <div className="min-w-0">
                    <p className="text-zinc-900 truncate">{log.entity_type} {log.action}</p>
                    <p className="text-xs text-zinc-400 truncate">{log.user_email || 'System'}</p>
                  </div>
                  <span className="text-xs text-zinc-400 whitespace-nowrap">{log.created_at ? new Date(log.created_at).toLocaleString() : '-'}</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
