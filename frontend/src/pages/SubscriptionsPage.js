import { useState, useEffect } from 'react';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Card, CardContent } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { useBusiness } from '@/components/layout/DashboardLayout';
import { Receipt, MoreHorizontal, ArrowUpDown, XCircle, PlayCircle, PauseCircle, MapPin, Users, Blocks, Plug, Building2, CalendarDays } from 'lucide-react';

const STATUS_COLORS = {
  active: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  trial: 'bg-blue-100 text-blue-700 border-blue-200',
  expired: 'bg-red-100 text-red-700 border-red-200',
  cancelled: 'bg-zinc-100 text-zinc-600 border-zinc-200',
  suspended: 'bg-amber-100 text-amber-700 border-amber-200',
};

function UsageBar({ label, used, max, icon: Icon }) {
  const unlimited = max === 'unlimited';
  const numericMax = unlimited ? 999 : Number(max || 0);
  const pct = unlimited ? (used > 0 ? 5 : 0) : numericMax > 0 ? Math.min((used / numericMax) * 100, 100) : 0;
  const isOver = !unlimited && numericMax > 0 && used > numericMax;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="flex items-center gap-1.5 text-zinc-600"><Icon className="h-3 w-3" />{label}</span>
        <span className={`font-medium ${isOver ? 'text-red-600' : 'text-zinc-900'}`}>
          {used} / {unlimited ? '\u221e' : numericMax}
        </span>
      </div>
      <Progress value={pct} className={`h-1.5 ${isOver ? '[&>div]:bg-red-500' : '[&>div]:bg-blue-500'}`} />
    </div>
  );
}

export default function SubscriptionsPage() {
  const { businesses } = useBusiness();
  const [subscriptions, setSubscriptions] = useState([]);
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editingSub, setEditingSub] = useState(null);
  const [entitlements, setEntitlements] = useState(null);
  const [detailBizId, setDetailBizId] = useState(null);
  const [form, setForm] = useState({ plan_id: '', billing_cycle: 'monthly', status: 'active' });

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [subsRes, plansRes] = await Promise.all([api.get('/subscriptions'), api.get('/plans')]);
        setSubscriptions(subsRes.data);
        setPlans(plansRes.data);
      } catch (err) { toast.error('Failed to load subscriptions'); }
      finally { setLoading(false); }
    };
    fetchData();
  }, []);

  const refresh = async () => {
    const { data } = await api.get('/subscriptions');
    setSubscriptions(data);
  };

  const openEdit = (sub) => {
    setEditingSub(sub);
    setForm({ plan_id: sub.plan_id, billing_cycle: sub.billing_cycle, status: sub.status });
    setSheetOpen(true);
  };

  const viewEntitlements = async (bizId) => {
    try {
      const { data } = await api.get(`/businesses/${bizId}/entitlements`);
      setEntitlements(data);
      setDetailBizId(bizId);
    } catch { toast.error('Failed to load entitlements'); }
  };

  const handleUpdate = async (e) => {
    e.preventDefault();
    if (!editingSub) return;
    try {
      await api.put(`/subscriptions/${editingSub.id}`, form);
      toast.success('Subscription updated');
      setSheetOpen(false);
      refresh();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const changeStatus = async (sub, status) => {
    try {
      if (status === 'cancelled') {
        await api.post(`/subscriptions/${sub.id}/cancel`);
      } else {
        await api.put(`/subscriptions/${sub.id}`, { status });
      }
      toast.success(`Subscription ${status}`);
      refresh();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const assignPlan = async (bizId) => {
    if (plans.length === 0) { toast.error('No plans available'); return; }
    try {
      await api.post('/subscriptions', { business_id: bizId, plan_id: plans[0].id, billing_cycle: 'monthly', status: 'trial' });
      toast.success('Subscription created');
      refresh();
    } catch (err) { toast.error(formatApiError(err)); }
  };

  const unsubscribedBusinesses = businesses.filter(b => !subscriptions.find(s => s.business_id === b.id));
  const limit = (code, legacyCode) => entitlements?.limits?.[code] ?? entitlements?.limits?.[legacyCode] ?? 0;

  return (
    <div className="space-y-6" data-testid="subscriptions-page">
      <div>
        <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Subscriptions</h1>
        <p className="text-sm text-zinc-500 mt-0.5">Manage business plan assignments, billing cycles, and entitlements</p>
      </div>

      {/* Entitlements Detail Panel */}
      {entitlements && detailBizId && (
        <Card className="shadow-sm border-blue-200 bg-blue-50/30">
          <CardContent className="p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-semibold text-zinc-900 font-heading">
                  Usage & Entitlements: {subscriptions.find(s => s.business_id === detailBizId)?.business_name}
                </h3>
                <div className="flex items-center gap-2 mt-1">
                  <Badge className={`text-[10px] ${STATUS_COLORS[entitlements.subscription?.status] || ''}`}>{entitlements.subscription?.status}</Badge>
                  <span className="text-xs text-zinc-500">{entitlements.plan?.name} plan</span>
                  {entitlements.subscription?.trial_end && (
                    <span className="text-xs text-zinc-400 flex items-center gap-1"><CalendarDays className="h-3 w-3" />Trial ends {new Date(entitlements.subscription.trial_end).toLocaleDateString()}</span>
                  )}
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={() => { setEntitlements(null); setDetailBizId(null); }} className="text-xs">Close</Button>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <UsageBar label="Outlets" used={entitlements.usage?.outlets || 0} max={limit('outlets.max', 'max_outlets')} icon={MapPin} />
              <UsageBar label="Users" used={entitlements.usage?.users || 0} max={limit('users.max', 'max_users')} icon={Users} />
              <UsageBar label="Modules" used={entitlements.usage?.modules || 0} max={limit('modules.max', 'max_modules')} icon={Blocks} />
              <UsageBar label="Integrations" used={entitlements.usage?.integrations || 0} max={limit('integrations.max', 'max_integrations')} icon={Plug} />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Unsubscribed Businesses */}
      {unsubscribedBusinesses.length > 0 && (
        <Card className="shadow-sm border-amber-200 bg-amber-50/30">
          <CardContent className="p-4">
            <p className="text-sm font-medium text-amber-800 mb-2">Businesses without a subscription</p>
            <div className="flex flex-wrap gap-2">
              {unsubscribedBusinesses.map(b => (
                <Button key={b.id} variant="outline" size="sm" onClick={() => assignPlan(b.id)} className="text-xs gap-1.5 border-amber-300 text-amber-700 hover:bg-amber-100" data-testid={`assign-plan-${b.slug}`}>
                  <Building2 className="h-3 w-3" />{b.name} — Assign Plan
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Subscriptions Table */}
      <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
        <Table>
          <TableHeader>
            <TableRow className="bg-zinc-50 hover:bg-zinc-50">
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Business</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Plan</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Status</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Cycle</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Period End</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Price</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={7} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
            ) : subscriptions.length === 0 ? (
              <TableRow><TableCell colSpan={7} className="text-center py-12 text-zinc-400">No subscriptions yet</TableCell></TableRow>
            ) : subscriptions.map(sub => (
              <TableRow key={sub.id} className="hover:bg-zinc-50/50" data-testid={`subscription-row-${sub.business_slug}`}>
                <TableCell className="font-medium text-zinc-900 py-3">{sub.business_name}</TableCell>
                <TableCell className="py-3"><Badge variant="outline" className="text-[11px]">{sub.plan_name}</Badge></TableCell>
                <TableCell className="py-3"><Badge className={`text-[11px] ${STATUS_COLORS[sub.status] || 'bg-zinc-100'}`}>{sub.status}</Badge></TableCell>
                <TableCell className="text-zinc-600 py-3 text-sm capitalize">{sub.billing_cycle}</TableCell>
                <TableCell className="text-zinc-500 py-3 text-xs font-mono">{sub.current_period_end ? new Date(sub.current_period_end).toLocaleDateString() : '-'}</TableCell>
                <TableCell className="text-zinc-700 py-3 text-sm font-medium">
                  ${sub.plan_pricing?.[sub.billing_cycle] || 0}/{sub.billing_cycle === 'monthly' ? 'mo' : 'yr'}
                </TableCell>
                <TableCell className="py-3">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="h-7 w-7"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-44">
                      <DropdownMenuItem onClick={() => viewEntitlements(sub.business_id)} data-testid={`view-usage-${sub.business_slug}`}><Blocks className="h-3.5 w-3.5 mr-2" />View Usage</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => openEdit(sub)}><ArrowUpDown className="h-3.5 w-3.5 mr-2" />Change Plan</DropdownMenuItem>
                      {sub.status !== 'active' && <DropdownMenuItem onClick={() => changeStatus(sub, 'active')}><PlayCircle className="h-3.5 w-3.5 mr-2" />Activate</DropdownMenuItem>}
                      {sub.status === 'active' && <DropdownMenuItem onClick={() => changeStatus(sub, 'suspended')}><PauseCircle className="h-3.5 w-3.5 mr-2" />Suspend</DropdownMenuItem>}
                      {sub.status !== 'cancelled' && <DropdownMenuItem onClick={() => changeStatus(sub, 'cancelled')} className="text-red-600 focus:text-red-600"><XCircle className="h-3.5 w-3.5 mr-2" />Cancel</DropdownMenuItem>}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Edit Subscription Sheet */}
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="sm:max-w-md">
          <SheetHeader><SheetTitle className="font-heading">Update Subscription</SheetTitle></SheetHeader>
          <form onSubmit={handleUpdate} className="space-y-5 mt-6">
            <div className="p-3 bg-zinc-50 rounded-md text-sm">
              <span className="text-zinc-500">Business: </span>
              <span className="font-medium text-zinc-900">{editingSub?.business_name}</span>
            </div>
            <div className="space-y-2">
              <Label>Plan</Label>
              <Select value={form.plan_id} onValueChange={v => setForm({...form, plan_id: v})}>
                <SelectTrigger data-testid="sub-plan-select"><SelectValue placeholder="Select plan" /></SelectTrigger>
                <SelectContent>
                  {plans.map(p => (
                    <SelectItem key={p.id} value={p.id}>{p.name} — ${p.pricing?.monthly}/mo</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Billing Cycle</Label>
              <Select value={form.billing_cycle} onValueChange={v => setForm({...form, billing_cycle: v})}>
                <SelectTrigger data-testid="sub-cycle-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="monthly">Monthly</SelectItem>
                  <SelectItem value="yearly">Yearly</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Status</Label>
              <Select value={form.status} onValueChange={v => setForm({...form, status: v})}>
                <SelectTrigger data-testid="sub-status-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {['active', 'trial', 'suspended', 'expired', 'cancelled'].map(s => (
                    <SelectItem key={s} value={s} className="capitalize">{s}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" data-testid="sub-submit-btn">Update Subscription</Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
