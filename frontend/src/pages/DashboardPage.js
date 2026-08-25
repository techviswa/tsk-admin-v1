import { useState, useEffect } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
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
  { key: 'total_revenue', label: 'Revenue', icon: IndianRupee, color: 'text-emerald-600 bg-emerald-50', currency: true },
  { key: 'total_products', label: 'Products', icon: Package, color: 'text-violet-600 bg-violet-50' },
  { key: 'total_inventory_items', label: 'Inventory Items', icon: Boxes, color: 'text-amber-600 bg-amber-50' },
];

const formatStatValue = (value, config) => {
  const number = Number(value || 0);
  if (config.currency) {
    return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(number);
  }
  return new Intl.NumberFormat('en-IN').format(number);
};

export default function DashboardPage() {
  const { selectedBusiness } = useBusiness();
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const fetchStats = async () => {
      setLoading(true);
      setError('');
      try {
        const params = selectedBusiness ? { business_id: selectedBusiness.id } : {};
        const { data } = await api.get('/dashboard/stats', { params });
        if (!cancelled) setStats(data);
      } catch (err) {
        console.error('Failed to fetch stats', err);
        if (!cancelled) {
          const message = formatApiError(err);
          setError(message);
          setStats({
            total_businesses: 0,
            total_outlets: 0,
            active_modules: 0,
            total_users: 0,
            total_feature_flags: 0,
            total_integrations: 0,
            total_products: 0,
            total_orders: 0,
            total_bills: 0,
            total_inventory_items: 0,
            total_staff_records: 0,
            total_tables: 0,
            total_kitchen_tickets: 0,
            total_revenue: 0,
            recent_activity: [],
          });
          toast.error(`Failed to load overview: ${message}`);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchStats();
    return () => { cancelled = true; };
  }, [selectedBusiness]);

  if (loading && !stats) {
    return (
      <div className="space-y-4">
        <div className="h-10 w-64 bg-zinc-200 rounded animate-pulse" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[1,2,3,4].map(i => <div key={i} className="h-28 bg-zinc-200 rounded-lg animate-pulse" />)}
        </div>
      </div>
    );
  }

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
        { key: 'recent_activity_count', label: 'Recent Events', icon: Activity, color: 'bg-teal-50 text-teal-600', value: stats.recent_activity?.length ?? 0 },
      ];

  return (
    <div className="space-y-8" data-testid="dashboard-page">
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Overview could not refresh: {error}
        </div>
      )}
      <div>
        <h1 className="text-3xl sm:text-4xl tracking-tight font-semibold text-zinc-950 font-heading">
          {selectedBusiness ? selectedBusiness.name : 'Platform Overview'}
        </h1>
        <p className="text-sm text-zinc-500 mt-1">
          {selectedBusiness
            ? `${(selectedBusiness.type || 'business').charAt(0).toUpperCase() + (selectedBusiness.type || 'business').slice(1)} \u2022 ${selectedBusiness.plan || 'starter'} plan \u2022 ${selectedBusiness.pos_synced ? 'POS synced' : 'AdminCore'}`
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
                  <p className="text-3xl font-semibold text-zinc-900 mt-1 font-heading">{formatStatValue(stats[key], config)}</p>
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
                    <span className="font-medium text-zinc-700">{log.entity_type}</span>
                  </span>
                  <span className="text-xs text-zinc-400 whitespace-nowrap shrink-0">
                    {new Date(log.created_at).toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="px-6 py-12 text-center text-sm text-zinc-400">
              No recent activity to display
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
