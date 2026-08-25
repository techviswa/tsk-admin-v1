import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import {
  BarChart3, Bell, Boxes, CalendarClock, CalendarDays, ChefHat, FileLock2, HandCoins, Plug,
  Printer, Receipt, RefreshCw, ShieldCheck, ShoppingBag, Table2, Tags, Trash2,
  Truck, Users, WalletCards, Pencil, Plus, Eye, Download
} from 'lucide-react';

var RESOURCE_CONFIG = {
  'sales-orders': {
    title: 'POS Orders / Sales',
    icon: ShoppingBag,
    primary: 'Order / Sale',
    category: 'Channel',
    owner: 'Customer / Cashier',
    contact: 'Receipt / Ref',
    amount: 'Order Total',
    notes: 'Order Notes',
  },
  inventory: {
    title: 'Inventory Management',
    icon: Boxes,
    primary: 'Stock Item',
    category: 'Unit / Category',
    owner: 'Vendor',
    contact: 'SKU / Batch',
    amount: 'Stock Qty',
    notes: 'Stock Notes',
  },
  customers: {
    title: 'Customers / CRM',
    icon: Users,
    primary: 'Customer Name',
    category: 'Segment',
    owner: 'Owner / Account',
    contact: 'Phone / Email',
    amount: 'Lifetime Value',
    notes: 'Customer Notes',
  },
  tables: {
    title: 'Table Management',
    icon: Table2,
    primary: 'Table / Area',
    category: 'Floor / Area',
    owner: 'Assigned Waiter',
    contact: 'QR / Reservation Ref',
    amount: 'Seats',
    notes: 'Table Notes',
  },
  reservations: {
    title: 'Reservations',
    icon: CalendarDays,
    primary: 'Guest / Reservation',
    category: 'Booking Source',
    owner: 'Assigned Table',
    contact: 'Phone / Reservation Ref',
    amount: 'Guests',
    notes: 'Reservation Notes',
  },
  'kitchen-kot': {
    title: 'Kitchen / KOT',
    icon: ChefHat,
    primary: 'Ticket / Station',
    category: 'Kitchen Section',
    owner: 'Chef',
    contact: 'Order Ref',
    amount: 'Items',
    notes: 'Preparation Notes',
  },
  payments: {
    title: 'Payments',
    icon: WalletCards,
    primary: 'Payment Ref',
    category: 'Method',
    owner: 'Customer / Cashier',
    contact: 'Provider Ref',
    amount: 'Amount',
    notes: 'Payment Notes',
  },
  bills: {
    title: 'Bills / Invoices',
    icon: Receipt,
    primary: 'Bill / Invoice',
    category: 'Bill Type',
    owner: 'Customer / Cashier',
    contact: 'Invoice / Receipt Ref',
    amount: 'Bill Total',
    notes: 'Bill Notes',
  },
  'reports-analytics': {
    title: 'Reports & Analytics',
    icon: BarChart3,
    primary: 'Report Name',
    category: 'Report Type',
    owner: 'Owner',
    contact: 'Schedule',
    amount: 'Value',
    notes: 'Report Notes',
  },
  'taxes-charges': {
    title: 'Taxes & Charges',
    icon: Receipt,
    primary: 'Tax / Charge',
    category: 'Type',
    owner: 'Applies To',
    contact: 'Tax Code',
    amount: 'Rate / Amount',
    notes: 'Rule Notes',
  },
  'discounts-coupons': {
    title: 'Discounts / Coupons',
    icon: Tags,
    primary: 'Discount / Coupon',
    category: 'Discount Type',
    owner: 'Audience',
    contact: 'Coupon Code',
    amount: 'Value',
    notes: 'Rules',
  },
  'staff-shifts': {
    title: 'Staff Shifts / Attendance',
    icon: CalendarClock,
    primary: 'Shift Name',
    category: 'Role',
    owner: 'Staff Member',
    contact: 'Shift Time',
    amount: 'Hours',
    notes: 'Attendance Notes',
  },
  'suppliers-purchasing': {
    title: 'Suppliers / Purchasing',
    icon: Truck,
    primary: 'Supplier / PO',
    category: 'Purchase Type',
    owner: 'Supplier',
    contact: 'PO / Invoice Ref',
    amount: 'PO Value',
    notes: 'Purchase Notes',
  },
  expenses: {
    title: 'Expenses',
    icon: HandCoins,
    primary: 'Expense',
    category: 'Expense Type',
    owner: 'Submitted By',
    contact: 'Bill Ref',
    amount: 'Amount',
    notes: 'Expense Notes',
  },
  'hardware-printers': {
    title: 'Hardware / Printer Settings',
    icon: Printer,
    primary: 'Device Name',
    category: 'Device Type',
    owner: 'Outlet / Counter',
    contact: 'IP / Port',
    amount: 'Priority',
    notes: 'Device Notes',
  },
  'role-permissions': {
    title: 'Role Permissions Matrix',
    icon: ShieldCheck,
    primary: 'Role / Policy',
    category: 'Module',
    owner: 'Role Owner',
    contact: 'Permission Key',
    amount: 'Rules Count',
    notes: 'Permission Notes',
  },
  notifications: {
    title: 'Notifications',
    icon: Bell,
    primary: 'Notification Rule',
    category: 'Channel',
    owner: 'Audience',
    contact: 'Trigger',
    amount: 'Priority',
    notes: 'Message / Rule',
  },
  'import-export': {
    title: 'Import / Export',
    icon: Download,
    primary: 'Job Name',
    category: 'Data Type',
    owner: 'Requested By',
    contact: 'File / Source',
    amount: 'Rows',
    notes: 'Job Notes',
  },
  'audit-security': {
    title: 'Audit & Security',
    icon: FileLock2,
    primary: 'Security Event',
    category: 'Event Type',
    owner: 'Actor',
    contact: 'IP / Device',
    amount: 'Risk Score',
    notes: 'Investigation Notes',
  },
  'integrations-webhooks': {
    title: 'Integrations / Webhooks',
    icon: Plug,
    primary: 'Integration / Webhook',
    category: 'Provider',
    owner: 'Owner',
    contact: 'Endpoint / Key',
    amount: 'Failure Count',
    notes: 'Integration Notes',
  },
};

const STATUS_CLASSES = {
  active: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  paid: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  reconciled: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  available: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  ready: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  online: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  connected: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  completed: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  none: 'bg-zinc-100 text-zinc-600 border-zinc-200',
  partial: 'bg-amber-100 text-amber-700 border-amber-200',
  requested: 'bg-amber-100 text-amber-700 border-amber-200',
  approved: 'bg-blue-100 text-blue-700 border-blue-200',
  scheduled: 'bg-blue-100 text-blue-700 border-blue-200',
  processing: 'bg-blue-100 text-blue-700 border-blue-200',
  open: 'bg-blue-100 text-blue-700 border-blue-200',
  pending: 'bg-amber-100 text-amber-700 border-amber-200',
  low_stock: 'bg-amber-100 text-amber-700 border-amber-200',
  preparing: 'bg-amber-100 text-amber-700 border-amber-200',
  failing: 'bg-red-100 text-red-700 border-red-200',
  failed: 'bg-red-100 text-red-700 border-red-200',
  blocked: 'bg-red-100 text-red-700 border-red-200',
  disabled: 'bg-zinc-100 text-zinc-600 border-zinc-200',
  inactive: 'bg-zinc-100 text-zinc-600 border-zinc-200',
};

const emptyForm = {
  title: '',
  business_id: '',
  outlet_id: '',
  status: '',
  category: '',
  owner_name: '',
  contact: '',
  amount: '',
  due_date: '',
  notes: '',
  payment_status: '',
  refund_status: '',
  payment_method: '',
  receipt_number: '',
  invoice_number: '',
  order_items_text: '',
  movement_type: '',
  movement_quantity: '',
  reorder_level: '',
  stock_by_outlet_text: '',
  phone: '',
  email: '',
  loyalty_points: '',
  order_history_text: '',
  dining_area: '',
  table_status: '',
  table_qr_code: '',
  reservations_text: '',
  ticket_items_text: '',
  chef_name: '',
  item_statuses_text: '',
  report_type: '',
  tax_rate: '',
  service_charge: '',
  packaging_charge: '',
  delivery_charge: '',
  tax_mode: '',
  coupon_code: '',
  discount_type: '',
  discount_value: '',
  applies_to: '',
  usage_limit: '',
};

function statusBadge(value) {
  const normalized = String(value || 'unknown').toLowerCase();
  return <Badge className={`text-[11px] ${STATUS_CLASSES[normalized] || 'bg-zinc-100 text-zinc-600 border-zinc-200'}`}>{normalized.replace(/_/g, ' ')}</Badge>;
}

function formatDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function formatAmount(value) {
  if (value === undefined || value === null || value === '') return '-';
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value;
}

function orderItemsToText(items = []) {
  return (items || []).map(item => `${item.name || ''}, ${item.quantity || 1}, ${item.price || 0}`).join('\n');
}

function textToOrderItems(value) {
  return String(value || '').split('\n').map(line => {
    const [name, quantity, price] = line.split(',').map(part => part.trim());
    if (!name) return null;
    return { name, quantity: Number(quantity || 1), price: Number(price || 0) };
  }).filter(Boolean);
}

function jsonText(value) {
  if (!value || Object.keys(value || {}).length === 0) return '';
  return JSON.stringify(value, null, 2);
}

function parseJsonText(value, fallback) {
  if (!value) return fallback;
  return JSON.parse(value);
}

export default function POSAdminPage() {
  const { resource = 'sales-orders' } = useParams();
  const config = RESOURCE_CONFIG[resource] || RESOURCE_CONFIG['sales-orders'];
  const Icon = config.icon;
  const { businesses, selectedBusiness } = useBusiness();
  const [data, setData] = useState({ records: [], statuses: [], summary: { total: 0, amount_total: 0, status_counts: [] } });
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [filters, setFilters] = useState({ search: '', status: 'all', business_id: '', outlet_id: '', date_from: '', date_to: '' });
  const [paymentReport, setPaymentReport] = useState(null);
  const [inventoryMovements, setInventoryMovements] = useState([]);
  const [customerHistory, setCustomerHistory] = useState(null);
  const [kitchenPerformance, setKitchenPerformance] = useState(null);
  const [reportsSummary, setReportsSummary] = useState(null);

  const effectiveBusinessId = filters.business_id || selectedBusiness?.id || '';

  const fetchRecords = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        search: filters.search || undefined,
        status: filters.status !== 'all' ? filters.status : undefined,
        business_id: effectiveBusinessId || undefined,
        outlet_id: filters.outlet_id || undefined,
        date_from: filters.date_from || undefined,
        date_to: filters.date_to || undefined,
      };
      const { data: result } = await api.get(`/pos-admin/${resource}`, { params });
      setData(result);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, [effectiveBusinessId, filters.date_from, filters.date_to, filters.outlet_id, filters.search, filters.status, resource]);

  useEffect(() => { fetchRecords(); }, [fetchRecords]);

  useEffect(() => {
    if (resource !== 'payments') {
      setPaymentReport(null);
      return;
    }
    async function fetchPaymentReport() {
      try {
        const params = effectiveBusinessId ? { business_id: effectiveBusinessId } : {};
        const { data: result } = await api.get('/pos-admin/payments/report', { params });
        setPaymentReport(result);
      } catch {
        setPaymentReport(null);
      }
    }
    fetchPaymentReport();
  }, [effectiveBusinessId, resource]);

  useEffect(() => {
    if (resource !== 'kitchen-kot') {
      setKitchenPerformance(null);
      return;
    }
    async function fetchKitchenPerformance() {
      try {
        const params = effectiveBusinessId ? { business_id: effectiveBusinessId } : {};
        const { data: result } = await api.get('/pos-admin/kitchen-kot/performance', { params });
        setKitchenPerformance(result);
      } catch {
        setKitchenPerformance(null);
      }
    }
    fetchKitchenPerformance();
  }, [effectiveBusinessId, resource]);

  useEffect(() => {
    if (resource !== 'reports-analytics') {
      setReportsSummary(null);
      return;
    }
    async function fetchReportsSummary() {
      try {
        const params = effectiveBusinessId ? { business_id: effectiveBusinessId } : {};
        const { data: result } = await api.get('/pos-admin/reports-analytics/summary', { params });
        setReportsSummary(result);
      } catch {
        setReportsSummary(null);
      }
    }
    fetchReportsSummary();
  }, [effectiveBusinessId, resource]);

  const statuses = useMemo(() => data.statuses || [], [data.statuses]);

  const openCreate = () => {
    setEditing(null);
    setInventoryMovements([]);
    setForm({
      ...emptyForm,
      business_id: selectedBusiness?.id || '',
      status: statuses[0] || 'active',
      payment_status: resource === 'sales-orders' ? 'pending' : '',
      refund_status: resource === 'payments' ? 'none' : '',
      payment_method: resource === 'payments' ? 'cash' : '',
      movement_type: resource === 'inventory' ? 'stock_in' : '',
      table_status: resource === 'tables' ? 'available' : '',
      chef_name: '',
      report_type: resource === 'reports-analytics' ? 'sales' : '',
      tax_mode: resource === 'taxes-charges' ? 'exclusive' : '',
      discount_type: resource === 'discounts-coupons' ? 'percentage' : '',
      applies_to: resource === 'discounts-coupons' ? 'item' : '',
    });
    setSheetOpen(true);
  };

  const openEdit = async (record) => {
    setEditing(record);
    setForm({
      title: record.title || '',
      business_id: record.business_id || '',
      outlet_id: record.outlet_id || '',
      status: record.status || statuses[0] || 'active',
      category: record.category || '',
      owner_name: record.owner_name || '',
      contact: record.contact || '',
      amount: record.amount ?? '',
      due_date: record.due_date || '',
      notes: record.notes || '',
      payment_status: record.payment_status || '',
      refund_status: record.refund_status || '',
      payment_method: record.payment_method || '',
      receipt_number: record.receipt_number || '',
      invoice_number: record.invoice_number || '',
      order_items_text: orderItemsToText(record.order_items || []),
      movement_type: record.movement_type || '',
      movement_quantity: '',
      reorder_level: record.reorder_level ?? '',
      stock_by_outlet_text: jsonText(record.stock_by_outlet || {}),
      phone: record.phone || '',
      email: record.email || '',
      loyalty_points: record.loyalty_points ?? '',
      order_history_text: jsonText(record.order_history || []),
      dining_area: record.dining_area || '',
      table_status: record.table_status || '',
      table_qr_code: record.table_qr_code || '',
      reservations_text: jsonText(record.reservations || []),
      ticket_items_text: jsonText(record.ticket_items || []),
      chef_name: record.chef_name || '',
      item_statuses_text: jsonText(record.item_statuses || {}),
      report_type: record.report_type || '',
      tax_rate: record.tax_rate ?? '',
      service_charge: record.service_charge ?? '',
      packaging_charge: record.packaging_charge ?? '',
      delivery_charge: record.delivery_charge ?? '',
      tax_mode: record.tax_mode || '',
      coupon_code: record.coupon_code || '',
      discount_type: record.discount_type || '',
      discount_value: record.discount_value ?? '',
      applies_to: record.applies_to || '',
      usage_limit: record.usage_limit ?? '',
    });
    if (resource === 'inventory') {
      try {
        const { data: result } = await api.get(`/pos-admin/inventory/${record.id}/movements`);
        setInventoryMovements(result);
      } catch {
        setInventoryMovements([]);
      }
    } else {
      setInventoryMovements([]);
    }
    if (resource === 'customers') {
      try {
        const { data: result } = await api.get(`/pos-admin/customers/${record.id}/order-history`);
        setCustomerHistory(result);
      } catch {
        setCustomerHistory(null);
      }
    } else {
      setCustomerHistory(null);
    }
    setSheetOpen(true);
  };

  const saveRecord = async (event) => {
    event.preventDefault();
    let stockByOutlet = {};
    if (form.stock_by_outlet_text) {
      try {
        stockByOutlet = JSON.parse(form.stock_by_outlet_text);
      } catch {
        toast.error('Outlet-wise stock must be valid JSON');
        return;
      }
    }
    let orderHistory = [];
    let reservations = [];
    let ticketItems = [];
    let itemStatuses = {};
    try {
      orderHistory = parseJsonText(form.order_history_text, []);
      reservations = parseJsonText(form.reservations_text, []);
      ticketItems = parseJsonText(form.ticket_items_text, []);
      itemStatuses = parseJsonText(form.item_statuses_text, {});
    } catch {
      toast.error('Phase 2 JSON fields must contain valid JSON');
      return;
    }
    const payload = {
      ...form,
      business_id: form.business_id || null,
      outlet_id: form.outlet_id || null,
      amount: form.amount === '' ? null : Number(form.amount),
      order_items: textToOrderItems(form.order_items_text),
      movement_quantity: form.movement_quantity === '' ? null : Number(form.movement_quantity),
      reorder_level: form.reorder_level === '' ? null : Number(form.reorder_level),
      stock_by_outlet: stockByOutlet,
      loyalty_points: form.loyalty_points === '' ? null : Number(form.loyalty_points),
      order_history: orderHistory,
      reservations,
      ticket_items: ticketItems,
      item_statuses: itemStatuses,
      tax_rate: form.tax_rate === '' ? null : Number(form.tax_rate),
      service_charge: form.service_charge === '' ? null : Number(form.service_charge),
      packaging_charge: form.packaging_charge === '' ? null : Number(form.packaging_charge),
      delivery_charge: form.delivery_charge === '' ? null : Number(form.delivery_charge),
      discount_value: form.discount_value === '' ? null : Number(form.discount_value),
      usage_limit: form.usage_limit === '' ? null : Number(form.usage_limit),
      metadata: {},
    };
    delete payload.order_items_text;
    delete payload.stock_by_outlet_text;
    delete payload.order_history_text;
    delete payload.reservations_text;
    delete payload.ticket_items_text;
    delete payload.item_statuses_text;
    try {
      if (editing) {
        await api.put(`/pos-admin/${resource}/${editing.id}`, payload);
        toast.success(`${config.title} record updated`);
      } else {
        await api.post(`/pos-admin/${resource}`, payload);
        toast.success(`${config.title} record created`);
      }
      setSheetOpen(false);
      fetchRecords();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  const exportReport = async (reportType, format) => {
    try {
      const params = { report_type: reportType, format };
      if (effectiveBusinessId) params.business_id = effectiveBusinessId;
      const response = await api.get('/pos-admin/reports-analytics/export', { params, responseType: 'blob' });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${reportType}-report.${format}`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  const deleteRecord = async (record) => {
    if (!window.confirm(`Delete "${record.title}"?`)) return;
    try {
      await api.delete(`/pos-admin/${resource}/${record.id}`);
      toast.success('Record deleted');
      fetchRecords();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  const updateStatus = async (record, status) => {
    try {
      await api.put(`/pos-admin/${resource}/${record.id}`, { status });
      toast.success('Status updated');
      fetchRecords();
    } catch (err) {
      toast.error(formatApiError(err));
    }
  };

  return (
    <div className="space-y-6" data-testid={`pos-admin-${resource}`}>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Icon className="h-6 w-6 text-blue-600" />
            <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">{config.title}</h1>
          </div>
          <p className="text-sm text-zinc-500 mt-1">
            {selectedBusiness ? `${selectedBusiness.name} operations` : 'Platform-wide POS operations'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={fetchRecords} disabled={loading} className="gap-1.5">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />Refresh
          </Button>
          <Button onClick={openCreate} className="gap-1.5 bg-blue-600 hover:bg-blue-700">
            <Plus className="h-3.5 w-3.5" />New Record
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Total Records</p><p className="text-2xl font-semibold text-zinc-900 mt-1">{data.summary?.total || 0}</p></CardContent></Card>
        <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Tracked Value</p><p className="text-2xl font-semibold text-zinc-900 mt-1">{formatAmount(data.summary?.amount_total || 0)}</p></CardContent></Card>
        <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Status Mix</p><div className="flex flex-wrap gap-1.5 mt-2">{(data.summary?.status_counts || []).slice(0, 4).map(row => <Badge key={row.status} variant="outline" className="text-[11px]">{row.status.replace(/_/g, ' ')}: {row.count}</Badge>)}</div></CardContent></Card>
      </div>

      {resource === 'payments' && paymentReport && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Payment Method Report</p><div className="flex flex-wrap gap-1.5 mt-2">{paymentReport.by_method?.map(row => <Badge key={row.method} variant="outline" className="text-[11px]">{row.method}: {formatAmount(row.amount)}</Badge>)}</div></CardContent></Card>
          <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Payment Status</p><div className="flex flex-wrap gap-1.5 mt-2">{paymentReport.by_status?.map(row => <Badge key={row.status} variant="outline" className="text-[11px]">{row.status}: {row.count}</Badge>)}</div></CardContent></Card>
          <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Refund Status</p><div className="flex flex-wrap gap-1.5 mt-2">{paymentReport.by_refund?.map(row => <Badge key={row.refund_status} variant="outline" className="text-[11px]">{row.refund_status}: {row.count}</Badge>)}</div></CardContent></Card>
        </div>
      )}

      {resource === 'kitchen-kot' && kitchenPerformance && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Kitchen Tickets</p><p className="text-2xl font-semibold text-zinc-900 mt-1">{kitchenPerformance.total_tickets || 0}</p></CardContent></Card>
          <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Ticket Status</p><div className="flex flex-wrap gap-1.5 mt-2">{kitchenPerformance.by_status?.map(row => <Badge key={row.status} variant="outline" className="text-[11px]">{row.status}: {row.count}</Badge>)}</div></CardContent></Card>
          <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Chef Performance</p><div className="flex flex-wrap gap-1.5 mt-2">{kitchenPerformance.by_chef?.map(row => <Badge key={row.chef} variant="outline" className="text-[11px]">{row.chef}: {row.tickets}</Badge>)}</div></CardContent></Card>
        </div>
      )}

      {resource === 'reports-analytics' && reportsSummary && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {Object.entries(reportsSummary).map(([key, value]) => (
            <Card key={key} className="shadow-sm border-zinc-200">
              <CardContent className="p-5">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">{key.replace(/_/g, ' ')}</p>
                <p className="text-2xl font-semibold text-zinc-900 mt-1">{value.count ?? value.active ?? value.total ?? 0}</p>
                <div className="flex gap-1.5 mt-3">
                  <Button variant="outline" size="sm" className="h-8 border-zinc-200" onClick={() => exportReport(key, 'csv')}>CSV</Button>
                  <Button variant="outline" size="sm" className="h-8 border-zinc-200" onClick={() => exportReport(key, 'pdf')}>PDF</Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Card className="shadow-sm border-zinc-200">
        <CardContent className="p-4 grid grid-cols-1 md:grid-cols-6 gap-3">
          <Input placeholder="Search records" value={filters.search} onChange={e => setFilters({ ...filters, search: e.target.value })} />
          <Select value={filters.status} onValueChange={status => setFilters({ ...filters, status })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {statuses.map(status => <SelectItem key={status} value={status}>{status.replace(/_/g, ' ')}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={filters.business_id || 'all'} onValueChange={value => setFilters({ ...filters, business_id: value === 'all' ? '' : value })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Current / all businesses</SelectItem>
              {(businesses || []).map(biz => <SelectItem key={biz.id} value={biz.id}>{biz.name}</SelectItem>)}
            </SelectContent>
          </Select>
          <Input placeholder="Outlet ID" value={filters.outlet_id} onChange={e => setFilters({ ...filters, outlet_id: e.target.value })} />
          <Input type="date" value={filters.date_from} onChange={e => setFilters({ ...filters, date_from: e.target.value })} />
          <Input type="date" value={filters.date_to} onChange={e => setFilters({ ...filters, date_to: e.target.value })} />
        </CardContent>
      </Card>

      <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden shadow-sm">
        <Table>
          <TableHeader>
            <TableRow className="bg-zinc-50 hover:bg-zinc-50">
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">{config.primary}</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Business</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">{config.category}</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">{config.owner}</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">{config.amount}</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Status</TableHead>
              <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Due / Created</TableHead>
              <TableHead className="w-32" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={8} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
            ) : data.records.length === 0 ? (
              <TableRow><TableCell colSpan={8} className="text-center py-12 text-zinc-400">No records yet</TableCell></TableRow>
            ) : data.records.map(record => (
              <TableRow key={record.id} className="hover:bg-zinc-50/50">
                <TableCell className="py-3">
                  <div>
                    <p className="font-medium text-zinc-900">{record.title}</p>
                    <p className="text-xs text-zinc-400">{record.contact || '-'}</p>
                    {resource === 'sales-orders' && (
                      <p className="text-xs text-zinc-400">
                        {record.order_items?.length || 0} items · Receipt {record.receipt_number || '-'} · Invoice {record.invoice_number || '-'}
                      </p>
                    )}
                    {resource === 'payments' && (
                      <p className="text-xs text-zinc-400">{record.payment_method || record.category || 'manual'} · refund {record.refund_status || 'none'}</p>
                    )}
                    {resource === 'inventory' && Number(record.reorder_level ?? -1) >= 0 && Number(record.amount || 0) <= Number(record.reorder_level || 0) && (
                      <Badge className="mt-1 text-[10px] bg-amber-100 text-amber-700 border-amber-200">low stock</Badge>
                    )}
                    {resource === 'customers' && (
                      <p className="text-xs text-zinc-400">{record.phone || '-'} · {record.email || '-'} · {record.loyalty_points || 0} points</p>
                    )}
                    {resource === 'tables' && (
                      <p className="text-xs text-zinc-400">{record.dining_area || '-'} · QR {record.table_qr_code || '-'} · {(record.reservations || []).length} reservations</p>
                    )}
                    {resource === 'kitchen-kot' && (
                      <p className="text-xs text-zinc-400">Chef {record.chef_name || '-'} · {(record.ticket_items || []).length} items</p>
                    )}
                    {resource === 'taxes-charges' && (
                      <p className="text-xs text-zinc-400">{record.tax_mode || '-'} · tax {record.tax_rate ?? 0}% · svc {record.service_charge ?? 0}</p>
                    )}
                    {resource === 'discounts-coupons' && (
                      <p className="text-xs text-zinc-400">{record.coupon_code || '-'} · {record.discount_type || '-'} · limit {record.usage_limit ?? '-'}</p>
                    )}
                  </div>
                </TableCell>
                <TableCell className="py-3 text-sm text-zinc-600">{record.business_name}</TableCell>
                <TableCell className="py-3 text-sm text-zinc-600">{record.category || '-'}</TableCell>
                <TableCell className="py-3 text-sm text-zinc-600">{record.owner_name || '-'}</TableCell>
                <TableCell className="py-3 text-sm text-zinc-600">{formatAmount(record.amount)}</TableCell>
                <TableCell className="py-3">
                  <div className="flex flex-col items-start gap-1">
                    {statusBadge(record.status)}
                    {resource === 'tables' && record.table_status && statusBadge(record.table_status)}
                    {resource === 'sales-orders' && record.payment_status && statusBadge(record.payment_status)}
                    {resource === 'payments' && record.refund_status && statusBadge(record.refund_status)}
                  </div>
                </TableCell>
                <TableCell className="py-3 text-sm text-zinc-600">{record.due_date || formatDate(record.created_at)}</TableCell>
                <TableCell className="py-3">
                  <div className="flex items-center justify-end gap-1.5">
                    <Select value={record.status || ''} onValueChange={status => updateStatus(record, status)}>
                      <SelectTrigger className="h-8 w-28"><SelectValue /></SelectTrigger>
                      <SelectContent>{statuses.map(status => <SelectItem key={status} value={status}>{status.replace(/_/g, ' ')}</SelectItem>)}</SelectContent>
                    </Select>
                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openEdit(record)}>{resource === 'sales-orders' ? <Eye className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}</Button>
                    <Button variant="ghost" size="icon" className="h-8 w-8 text-red-600 hover:text-red-700 hover:bg-red-50" onClick={() => deleteRecord(record)}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="sm:max-w-xl overflow-y-auto">
          <SheetHeader><SheetTitle className="font-heading">{editing ? `Edit ${config.title}` : `New ${config.title}`}</SheetTitle></SheetHeader>
          <form onSubmit={saveRecord} className="space-y-5 mt-6">
            <div className="space-y-2"><Label>{config.primary}</Label><Input value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} required /></div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Business</Label>
                <Select value={form.business_id || 'none'} onValueChange={value => setForm({ ...form, business_id: value === 'none' ? '' : value })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Platform-wide</SelectItem>
                    {(businesses || []).map(biz => <SelectItem key={biz.id} value={biz.id}>{biz.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Status</Label>
                <Select value={form.status || statuses[0] || 'active'} onValueChange={status => setForm({ ...form, status })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{statuses.map(status => <SelectItem key={status} value={status}>{status.replace(/_/g, ' ')}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="space-y-2"><Label>{config.category}</Label><Input value={form.category} onChange={e => setForm({ ...form, category: e.target.value })} /></div>
              <div className="space-y-2"><Label>{config.owner}</Label><Input value={form.owner_name} onChange={e => setForm({ ...form, owner_name: e.target.value })} /></div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="space-y-2"><Label>{config.contact}</Label><Input value={form.contact} onChange={e => setForm({ ...form, contact: e.target.value })} /></div>
              <div className="space-y-2"><Label>{config.amount}</Label><Input type="number" step="0.01" value={form.amount} onChange={e => setForm({ ...form, amount: e.target.value })} /></div>
            </div>
            {resource === 'sales-orders' && (
              <>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <Label>Payment Status</Label>
                    <Select value={form.payment_status || 'pending'} onValueChange={value => setForm({ ...form, payment_status: value })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {['pending', 'paid', 'partial', 'failed', 'refunded'].map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2"><Label>Receipt Number</Label><Input value={form.receipt_number} onChange={e => setForm({ ...form, receipt_number: e.target.value })} /></div>
                </div>
                <div className="space-y-2"><Label>Invoice Number</Label><Input value={form.invoice_number} onChange={e => setForm({ ...form, invoice_number: e.target.value })} /></div>
                <div className="space-y-2">
                  <Label>Order Items</Label>
                  <Textarea value={form.order_items_text} onChange={e => setForm({ ...form, order_items_text: e.target.value })} placeholder="Item name, quantity, price&#10;Paneer Wrap, 2, 149" className="min-h-28" />
                </div>
              </>
            )}
            {resource === 'payments' && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div className="space-y-2">
                  <Label>Payment Method</Label>
                  <Select value={form.payment_method || 'cash'} onValueChange={value => setForm({ ...form, payment_method: value, category: value })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {['cash', 'card', 'upi', 'manual'].map(value => <SelectItem key={value} value={value}>{value.toUpperCase()}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Payment Status</Label>
                  <Select value={form.payment_status || form.status || 'pending'} onValueChange={value => setForm({ ...form, payment_status: value, status: value })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {['pending', 'paid', 'failed', 'refunded', 'reconciled'].map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Refund Status</Label>
                  <Select value={form.refund_status || 'none'} onValueChange={value => setForm({ ...form, refund_status: value })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {['none', 'requested', 'approved', 'refunded', 'rejected'].map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            )}
            {resource === 'inventory' && (
              <>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="space-y-2">
                    <Label>Stock Movement</Label>
                    <Select value={form.movement_type || 'stock_in'} onValueChange={value => setForm({ ...form, movement_type: value })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {['stock_in', 'stock_out', 'adjustment', 'wastage'].map(value => <SelectItem key={value} value={value}>{value.replace(/_/g, ' ')}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2"><Label>Movement Quantity</Label><Input type="number" step="0.01" value={form.movement_quantity} onChange={e => setForm({ ...form, movement_quantity: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Reorder Level</Label><Input type="number" step="0.01" value={form.reorder_level} onChange={e => setForm({ ...form, reorder_level: e.target.value })} /></div>
                </div>
                <div className="space-y-2">
                  <Label>Outlet-wise Stock JSON</Label>
                  <Textarea value={form.stock_by_outlet_text} onChange={e => setForm({ ...form, stock_by_outlet_text: e.target.value })} placeholder={'{"outlet_id": 10}'} />
                </div>
                {inventoryMovements.length > 0 && (
                  <div className="space-y-2">
                    <Label>Inventory Movements</Label>
                    <div className="border border-zinc-200 rounded-md divide-y divide-zinc-100 max-h-40 overflow-auto">
                      {inventoryMovements.map(move => (
                        <div key={move.id} className="px-3 py-2 text-xs flex items-center justify-between gap-3">
                          <span className="font-medium text-zinc-700">{move.movement_type?.replace(/_/g, ' ')}</span>
                          <span className="text-zinc-600">{formatAmount(move.quantity)}</span>
                          <span className="text-zinc-400">{formatDate(move.created_at)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
            {resource === 'customers' && (
              <>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="space-y-2"><Label>Phone</Label><Input value={form.phone} onChange={e => setForm({ ...form, phone: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Email</Label><Input type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Loyalty Points</Label><Input type="number" step="1" value={form.loyalty_points} onChange={e => setForm({ ...form, loyalty_points: e.target.value })} /></div>
                </div>
                <div className="space-y-2">
                  <Label>Order History JSON</Label>
                  <Textarea value={form.order_history_text} onChange={e => setForm({ ...form, order_history_text: e.target.value })} placeholder={'[{"order":"INV-1001","total":450}]'} className="min-h-24" />
                </div>
                {customerHistory && (
                  <div className="space-y-2">
                    <Label>Matched Order History</Label>
                    <div className="border border-zinc-200 rounded-md divide-y divide-zinc-100 max-h-40 overflow-auto">
                      {[...(customerHistory.orders || []), ...(customerHistory.saved_history || [])].slice(0, 8).map((order, index) => (
                        <div key={order.id || index} className="px-3 py-2 text-xs flex items-center justify-between gap-3">
                          <span className="font-medium text-zinc-700">{order.title || order.order || order.contact || `Order ${index + 1}`}</span>
                          <span className="text-zinc-600">{formatAmount(order.amount || order.total)}</span>
                          <span className="text-zinc-400">{formatDate(order.created_at || order.date)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
            {resource === 'tables' && (
              <>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="space-y-2"><Label>Dining Area</Label><Input value={form.dining_area} onChange={e => setForm({ ...form, dining_area: e.target.value })} /></div>
                  <div className="space-y-2">
                    <Label>Table Status</Label>
                    <Select value={form.table_status || 'available'} onValueChange={value => setForm({ ...form, table_status: value, status: value })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>{['available', 'occupied', 'reserved', 'blocked'].map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2"><Label>Table QR Assignment</Label><Input value={form.table_qr_code} onChange={e => setForm({ ...form, table_qr_code: e.target.value })} /></div>
                </div>
                <div className="space-y-2">
                  <Label>Reservations JSON</Label>
                  <Textarea value={form.reservations_text} onChange={e => setForm({ ...form, reservations_text: e.target.value })} placeholder={'[{"name":"Ravi","time":"19:30","guests":4}]'} className="min-h-24" />
                </div>
              </>
            )}
            {resource === 'kitchen-kot' && (
              <>
                <div className="space-y-2"><Label>Chef</Label><Input value={form.chef_name} onChange={e => setForm({ ...form, chef_name: e.target.value })} /></div>
                <div className="space-y-2">
                  <Label>Kitchen Ticket Items JSON</Label>
                  <Textarea value={form.ticket_items_text} onChange={e => setForm({ ...form, ticket_items_text: e.target.value })} placeholder={'[{"name":"Dosa","qty":2,"status":"preparing"}]'} className="min-h-24" />
                </div>
                <div className="space-y-2">
                  <Label>Item-level Status JSON</Label>
                  <Textarea value={form.item_statuses_text} onChange={e => setForm({ ...form, item_statuses_text: e.target.value })} placeholder={'{"Dosa":"preparing","Coffee":"ready"}'} className="min-h-24" />
                </div>
              </>
            )}
            {resource === 'taxes-charges' && (
              <>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="space-y-2"><Label>Tax Rate %</Label><Input type="number" step="0.01" value={form.tax_rate} onChange={e => setForm({ ...form, tax_rate: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Service Charge</Label><Input type="number" step="0.01" value={form.service_charge} onChange={e => setForm({ ...form, service_charge: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Packaging Charge</Label><Input type="number" step="0.01" value={form.packaging_charge} onChange={e => setForm({ ...form, packaging_charge: e.target.value })} /></div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="space-y-2"><Label>Delivery Charge</Label><Input type="number" step="0.01" value={form.delivery_charge} onChange={e => setForm({ ...form, delivery_charge: e.target.value })} /></div>
                  <div className="space-y-2">
                    <Label>Tax Mode</Label>
                    <Select value={form.tax_mode || 'exclusive'} onValueChange={value => setForm({ ...form, tax_mode: value })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="inclusive">Inclusive tax</SelectItem>
                        <SelectItem value="exclusive">Exclusive tax</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </>
            )}
            {resource === 'discounts-coupons' && (
              <>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="space-y-2"><Label>Coupon Code</Label><Input value={form.coupon_code} onChange={e => setForm({ ...form, coupon_code: e.target.value.toUpperCase() })} /></div>
                  <div className="space-y-2">
                    <Label>Discount Type</Label>
                    <Select value={form.discount_type || 'percentage'} onValueChange={value => setForm({ ...form, discount_type: value })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="percentage">Percentage</SelectItem>
                        <SelectItem value="fixed">Fixed</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2"><Label>Discount Value</Label><Input type="number" step="0.01" value={form.discount_value} onChange={e => setForm({ ...form, discount_value: e.target.value })} /></div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <Label>Applies To</Label>
                    <Select value={form.applies_to || 'item'} onValueChange={value => setForm({ ...form, applies_to: value })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="item">Item</SelectItem>
                        <SelectItem value="category">Category</SelectItem>
                        <SelectItem value="cart">Cart</SelectItem>
                        <SelectItem value="customer">Customer</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2"><Label>Usage Limit</Label><Input type="number" step="1" value={form.usage_limit} onChange={e => setForm({ ...form, usage_limit: e.target.value })} /></div>
                </div>
              </>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="space-y-2"><Label>Outlet ID</Label><Input value={form.outlet_id} onChange={e => setForm({ ...form, outlet_id: e.target.value })} placeholder="Optional outlet id" /></div>
              <div className="space-y-2"><Label>Due Date</Label><Input type="date" value={form.due_date} onChange={e => setForm({ ...form, due_date: e.target.value })} /></div>
            </div>
            <div className="space-y-2"><Label>{config.notes}</Label><Textarea value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></div>
            <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700">{editing ? 'Update Record' : 'Create Record'}</Button>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
