import { useState, useEffect } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { entityLabel } from '@/lib/posOperations';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Building2, MapPin, Blocks, Users, Activity, Flag, Plug, Package, ReceiptText, IndianRupee, Boxes, Table2, ChefHat } from 'lucide-react';

const PLATFORM_STAT_CONFIG = [
  { key: 'total_businesses', label: 'Businesses', icon: Building2, color: 'text-blue-600 bg-blue-50' },
  { key: 'total_outlets', label: 'Active Outlets', icon: MapPin, color: 'text-emerald-600 bg-emerald-50' },
  { key: 'active_modules', label: 'Active Modules', icon: Blocks, color: 'text-violet-600 bg-violet-50' },
  { key: 'total_users', label: 'Total Users', icon: Users, color: 'text-amber-600 bg-amber-50' },
];

const BUSINESS_STAT_CONFIG = [
  { key: 'total_orders', label: 'POS Orders', icon: ReceiptText, color: 'text-blue-600 bg-blue-50' },
  { key: 'total_revenue', label: 'Sales after refunds', icon: IndianRupee, color: 'text-emerald-600 bg-emerald-50', currency: true },
  { key: 'total_products', label: 'Products', icon: Package, color: 'text-violet-600 bg-violet-50' },
  { key: 'total_inventory_items', label: 'Inventory Items', icon: Boxes, color: 'text-amber-600 bg-amber-50' },
];

export const formatStatValue = (value, config) => {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const number = Number(value);
  if (config.currency) {
    try {
      return new Intl.NumberFormat('en-IN', { ...(config.currencyCode && config.currencyCode !== 'unspecified' ? { style: 'currency', currency: config.currencyCode } : {}), minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(number);
    } catch {
      return new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(number);
    }
  }
  return new Intl.NumberFormat('en-IN').format(number);
};

export default function DashboardPage() {
  const { selectedBusiness } = useBusiness();
  const businessId = selectedBusiness?.id;
  const [snapshot, setSnapshot] = useState(null);
  const stats = snapshot && snapshot.businessId === businessId ? snapshot.data : null;
  const refreshedAt = snapshot && snapshot.businessId === businessId ? snapshot.refreshedAt : null;
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false, running = false;
    const fetchStats = async () => {
      if (running) return;
      running = true;
      setLoading(true);
      setError('');
      try {
        const params = businessId ? { business_id: businessId } : {};
        const { data } = await api.get('/dashboard/stats', { params });
        if (!cancelled) setSnapshot({ businessId, data, refreshedAt: new Date().toISOString() });
      } catch (err) {
        console.error('Failed to fetch stats', err);
        if (!cancelled) {
          const message = formatApiError(err);
          setError(message);
          setSnapshot(current => current && current.businessId === businessId ? current : { businessId, data: {}, refreshedAt: null });
        }
      } finally {
        running = false;
        if (!cancelled) setLoading(false);
      }
    };
    fetchStats();
    const timer = window.setInterval(() => { if (document.visibilityState !== 'hidden') fetchStats(); }, 30000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [businessId, reload]);

  if (!stats && (loading || snapshot?.businessId !== businessId)) {
    return (
      <div className="space-y-4">
        <div className="h-10 w-64 bg-zinc-200 rounded animate-pulse" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[1,2,3,4].map(i => <div key={i} className="h-28 bg-zinc-200 rounded-lg animate-pulse" />)}
        </div>
      </div>
    );
  }

  if (!stats) return null;
  const statConfig = selectedBusiness ? BUSINESS_STAT_CONFIG : PLATFORM_STAT_CONFIG;
  const secondaryStats = selectedBusiness
    ? [
        { key: 'total_outlets', label: 'Active Outlets', icon: MapPin, color: 'bg-sky-50 text-sky-600' },
        { key: 'total_tables', label: 'Tables', icon: Table2, color: 'bg-teal-50 text-teal-600' },
        { key: 'total_staff_records', label: 'Staff Records', icon: Users, color: 'bg-rose-50 text-rose-600' },
        { key: 'total_kitchen_tickets', label: 'Kitchen Tickets', icon: ChefHat, color: 'bg-orange-50 text-orange-600' },
      ]
    : [
        { key: 'total_feature_flags', label: 'Feature Flags', icon: Flag, color: 'bg-rose-50 text-rose-600' },
        { key: 'total_integrations', label: 'Integrations', icon: Plug, color: 'bg-sky-50 text-sky-600' },
        { key: 'recent_activity_count', label: 'Recent Events', icon: Activity, color: 'bg-teal-50 text-teal-600', value: stats.recent_activity?.length },
      ];

  return (
    <div className="space-y-8" data-testid="dashboard-page">
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Overview could not refresh: {error}
          {refreshedAt && <span> Showing the last confirmed overview from {new Date(refreshedAt).toLocaleString()}.</span>}
          <button className="ml-3 underline" onClick={() => setReload(value => value + 1)}>Retry overview</button>
        </div>
      )}
      <div>
        <h1 className="text-3xl sm:text-4xl tracking-tight font-semibold text-zinc-950 font-heading">
          {selectedBusiness ? selectedBusiness.name : 'Platform Overview'}
        </h1>
        <p className="text-sm text-zinc-500 mt-1">
          {selectedBusiness
            ? `${(selectedBusiness.type || 'business').charAt(0).toUpperCase() + (selectedBusiness.type || 'business').slice(1)} \u2022 ${selectedBusiness.plan || 'starter'} plan \u2022 ${selectedBusiness.pos_connection_state === 'setup_required' ? 'POS connection needs review' : selectedBusiness.pos_external_id ? 'Connected to POS · See POS Bridge for refresh status' : 'Managed in AdminCore'}`
            : 'Manage all businesses, modules, and configurations'}
        </p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {statConfig.map((config, idx) => {
          const { key, label, icon: Icon, color } = config;
          return (
          <Card key={key} className={`shadow-sm border-zinc-200 animate-fade-in stagger-${idx + 1}`}>
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">{label}</p>
                  <p className="text-3xl font-semibold text-zinc-900 mt-1 font-heading">{formatStatValue(stats[key], { ...config, currencyCode: stats.revenue_by_currency?.[0]?.currency })}</p>
                  {config.currency && <details className="text-xs text-zinc-500 mt-2"><summary className="cursor-pointer">How sales are calculated</summary><p className="mt-1">{stats.revenue_definition}</p></details>}
                  {config.currency && stats.revenue_by_currency?.length > 1 && stats.revenue_by_currency.map(row => <p key={row.currency} className="text-sm text-zinc-600">{row.currency}: {formatStatValue(row.amount, { currency: true, currencyCode: row.currency })}</p>)}
                </div>
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${color}`}>
                  <Icon className="h-5 w-5" />
                </div>
              </div>
            </CardContent>
          </Card>
          );
        })}
      </div>

      {/* Secondary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {secondaryStats.map(({ key, label, icon: Icon, color, value }) => (
          <Card key={key} className="shadow-sm border-zinc-200">
            <CardContent className="p-5 flex items-center gap-4">
              <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${color}`}>
                <Icon className="h-5 w-5" />
              </div>
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">{label}</p>
                <p className="text-2xl font-semibold text-zinc-900 font-heading">{formatStatValue(value ?? stats[key], {})}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Recent Activity */}
      <Card className="shadow-sm border-zinc-200">
        <CardHeader className="px-6 py-4 border-b border-zinc-100 flex flex-row items-center justify-between">
          <CardTitle className="text-base font-medium text-zinc-900 flex items-center gap-2">
            <Activity className="h-4 w-4 text-zinc-500" />
            Recent Activity
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {stats.recent_activity?.length > 0 ? (
            <div className="divide-y divide-zinc-100">
              {stats.recent_activity.map((log, i) => (
                <div key={i} className="px-6 py-3 flex items-center gap-3 text-sm hover:bg-zinc-50/50 transition-colors">
                  <div className={`w-2 h-2 rounded-full shrink-0 ${
                    log.action === 'created' ? 'bg-emerald-500' :
                    log.action === 'deleted' ? 'bg-red-500' :
                    log.action === 'updated' ? 'bg-blue-500' : 'bg-zinc-400'
                  }`} />
                  <span className="text-zinc-600 flex-1 min-w-0">
                    <span className="font-medium text-zinc-800">{log.user_email || 'System'}</span>
                    {' '}<span className="text-zinc-400">{log.action}</span>{' '}
                    <span className="font-medium text-zinc-700">{entityLabel(log.entity_type)}</span>
                  </span>
                  <span className="text-xs text-zinc-400 whitespace-nowrap shrink-0">
                    {new Date(log.created_at).toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="px-6 py-12 text-center text-sm text-zinc-400">
              {error ? 'Recent activity could not be loaded.' : 'No recent activity to display'}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
