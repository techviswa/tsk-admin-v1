import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { displayValue, recordCurrency } from '@/lib/posOperations';
import OperationalRecordDetails from '@/components/OperationalRecordDetails';
import RecordValueSummary from '@/components/RecordValueSummary';
import StructuredRecordEditor from '@/components/StructuredRecordEditor';
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
  return JSON.stringify(items || []);
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
  const monetary = ['sales-orders', 'payments', 'bills', 'customers', 'suppliers-purchasing', 'expenses'].includes(resource);
  const amountFormat = monetary ? 'money' : resource === 'inventory' ? 'quantity' : undefined;
  const { businesses, selectedBusiness } = useBusiness();
  const [snapshot, setSnapshot] = useState(null);
  const [failure, setFailure] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [filters, setFilters] = useState({ search: '', status: 'all', business_id: '', outlet_id: '', date_from: '', date_to: '' });
  const [paymentSnapshot, setPaymentReport] = useState(null);
  const [inventoryMovements, setInventoryMovements] = useState([]);
  const [customerHistory, setCustomerHistory] = useState(null);
  const [historyLimit, setHistoryLimit] = useState(8);
  const [outletOptions, setOutletOptions] = useState([]);
  const [outletError, setOutletError] = useState('');
  const [outletLookupVersion, setOutletLookupVersion] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [paging, setPaging] = useState({ scope: '', offset: 0 });
  const [kitchenSnapshot, setKitchenPerformance] = useState(null);
  const [reportSnapshot, setReportsSummary] = useState(null);

  const effectiveBusinessId = selectedBusiness?.id || filters.business_id || '';
  const context = `${resource}:${effectiveBusinessId}`;
  const filterScope = JSON.stringify([context, filters.search, filters.status, filters.outlet_id, filters.date_from, filters.date_to]);
  const offset = paging.scope === filterScope ? paging.offset : 0;
  const scope = JSON.stringify([filterScope, offset]);
  const data = snapshot?.scope === scope ? snapshot.value : { records: [], statuses: [], summary: {} };
  const hasSnapshot = snapshot?.scope === scope;
  const loadError = failure?.scope === scope ? failure.message : '';
  const paymentReport = paymentSnapshot?.scope === scope ? paymentSnapshot.value : null;
  const kitchenPerformance = kitchenSnapshot?.scope === scope ? kitchenSnapshot.value : null;
  const reportsSummary = reportSnapshot?.scope === scope ? reportSnapshot.value : null;
  const connected = Boolean(businesses.find(business => business.id === effectiveBusinessId)?.pos_external_id);
  const posOwned = record => connected || record?.pos_synced || Boolean(record?.pos_external_id);
  const requestVersion = useRef(0);
  const detailVersion = useRef(0);
  useEffect(() => {
    let cancelled = false;
    setOutletError('');
    setOutletOptions([]); setHistoryLimit(8);
    if (effectiveBusinessId && selectedBusiness?.pos_connection_state !== 'setup_required') (async () => {
      const rows = [];
      while (!cancelled) {
        const { data, headers } = await api.get(`/outlets/business/${effectiveBusinessId}`, { params: { status: 'all', limit: 500, offset: rows.length } });
        rows.push(...data);
        if (data.length < 500 || rows.length >= Number(headers?.['x-total-count'] || Infinity)) break;
      }
      if (!cancelled) setOutletOptions(rows);
    })().catch(error => { if (!cancelled) setOutletError(formatApiError(error)); });
    return () => { cancelled = true; };
  }, [effectiveBusinessId, selectedBusiness?.pos_connection_state, outletLookupVersion]);

  const fetchRecords = useCallback(async () => {
    const version = ++requestVersion.current;
    setLoading(true);
    setFailure(null);
    try {
      const params = {
        limit: 50, offset,
        search: filters.search || undefined,
        status: filters.status !== 'all' ? filters.status : undefined,
        business_id: effectiveBusinessId || undefined,
        outlet_id: filters.outlet_id || undefined,
        date_from: filters.date_from || undefined,
        date_to: filters.date_to || undefined,
      };
      const { data: result } = await api.get(`/pos-admin/${resource}`, { params });
      if (version === requestVersion.current) setSnapshot({ scope, value: result });
    } catch (err) {
      if (version === requestVersion.current) { setFailure({ scope, message: formatApiError(err) }); toast.error(formatApiError(err)); }
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, [effectiveBusinessId, filters.date_from, filters.date_to, filters.outlet_id, filters.search, filters.status, resource, scope, offset]);

  useEffect(() => { detailVersion.current += 1; setSheetOpen(false); setEditing(null); setCustomerHistory(null); setHistoryLoading(false); }, [context]);

  useEffect(() => {
    fetchRecords();
    return () => { requestVersion.current += 1; };
  }, [fetchRecords]);
  const refreshPending = ['pending', 'running', 'retrying'].includes(data?.refresh_job?.status);
  useEffect(() => {
    if (!refreshPending) return undefined;
    const timer = setInterval(fetchRecords, 10000);
    return () => clearInterval(timer);
  }, [fetchRecords, refreshPending]);

  useEffect(() => {
    let active = true;
    if (resource !== 'payments') {
      setPaymentReport(null);
      return;
    }
    async function fetchPaymentReport() {
      try {
        const params = { business_id: effectiveBusinessId || undefined, outlet_id: filters.outlet_id || undefined, date_from: filters.date_from || undefined, date_to: filters.date_to || undefined };
        const { data: result } = await api.get('/pos-admin/payments/report', { params });
        if (active) setPaymentReport({ scope, value: result });
      } catch {
        if (active) setPaymentReport(null);
      }
    }
    fetchPaymentReport();
    return () => { active = false; };
  }, [effectiveBusinessId, resource, scope, filters.outlet_id, filters.date_from, filters.date_to]);

  useEffect(() => {
    let active = true;
    if (resource !== 'kitchen-kot') {
      setKitchenPerformance(null);
      return;
    }
    async function fetchKitchenPerformance() {
      try {
        const params = { business_id: effectiveBusinessId || undefined, outlet_id: filters.outlet_id || undefined, date_from: filters.date_from || undefined, date_to: filters.date_to || undefined };
        const { data: result } = await api.get('/pos-admin/kitchen-kot/performance', { params });
        if (active) setKitchenPerformance({ scope, value: result });
      } catch {
        if (active) setKitchenPerformance(null);
      }
    }
    fetchKitchenPerformance();
    return () => { active = false; };
  }, [effectiveBusinessId, resource, scope, filters.outlet_id, filters.date_from, filters.date_to]);

  useEffect(() => {
    let active = true;
    if (resource !== 'reports-analytics') {
      setReportsSummary(null);
      return;
    }
    async function fetchReportsSummary() {
      try {
        const params = { business_id: effectiveBusinessId || undefined, outlet_id: filters.outlet_id || undefined, date_from: filters.date_from || undefined, date_to: filters.date_to || undefined };
        const { data: result } = await api.get('/pos-admin/reports-analytics/summary', { params });
        if (active) setReportsSummary({ scope, value: result });
      } catch {
        if (active) setReportsSummary(null);
      }
    }
    fetchReportsSummary();
    return () => { active = false; };
  }, [effectiveBusinessId, resource, scope, filters.outlet_id, filters.date_from, filters.date_to]);

  const statuses = useMemo(() => data.statuses || [], [data.statuses]);

  const openCreate = () => {
    detailVersion.current += 1;
    setCustomerHistory(null); setHistoryLimit(8);
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
    const detailRequest = ++detailVersion.current;
    setCustomerHistory(null); setHistoryLimit(8); setHistoryLoading(false);
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
        if (detailRequest !== detailVersion.current) return;
        setInventoryMovements(result);
      } catch {
        if (detailRequest !== detailVersion.current) return;
        setInventoryMovements([]);
      }
    } else {
      setInventoryMovements([]);
    }
    if (resource === 'customers') {
      try {
        const { data: result } = await api.get(`/pos-admin/customers/${record.id}/order-history`);
        if (detailRequest !== detailVersion.current) return;
        setCustomerHistory(result);
      } catch {
        if (detailRequest !== detailVersion.current) return;
        setCustomerHistory(null);
      }
    } else {
      setCustomerHistory(null);
    }
    if (detailRequest === detailVersion.current) setSheetOpen(true);
  };

  const showMoreHistory = async () => {
    const loaded = (customerHistory?.orders || []).length + (customerHistory?.saved_history || []).length;
    if (historyLimit < loaded) { setHistoryLimit(value => value + 20); return; }
    if (!editing || !customerHistory?.pagination?.has_more || historyLoading) return;
    const detailRequest = detailVersion.current;
    setHistoryLoading(true);
    try {
      const { data: result } = await api.get(`/pos-admin/customers/${editing.id}/order-history`, { params: { limit: 50, offset: customerHistory.orders.length } });
      if (detailRequest !== detailVersion.current) return;
      setCustomerHistory(current => ({ ...current, orders: [...current.orders, ...result.orders], pagination: result.pagination }));
      setHistoryLimit(value => value + 20);
    } catch (error) { if (detailRequest === detailVersion.current) toast.error(formatApiError(error)); }
    finally { if (detailRequest === detailVersion.current) setHistoryLoading(false); }
  };

  const saveRecord = async (event) => {
    event.preventDefault();
    let stockByOutlet = {};
    if (form.stock_by_outlet_text) {
      try {
        stockByOutlet = JSON.parse(form.stock_by_outlet_text);
      } catch {
        toast.error('The outlet stock entries could not be read. Reload the record before saving.');
        return;
      }
    }
    let orderHistory = [];
    let orderItems = [];
    let reservations = [];
    let ticketItems = [];
    let itemStatuses = {};
    try {
      orderItems = parseJsonText(form.order_items_text, []);
      orderHistory = parseJsonText(form.order_history_text, []);
      reservations = parseJsonText(form.reservations_text, []);
      ticketItems = parseJsonText(form.ticket_items_text, []);
      itemStatuses = parseJsonText(form.item_statuses_text, {});
    } catch {
      toast.error('Some saved record details could not be read. Reload the record before saving.');
      return;
    }
    const payload = {
      ...form,
      business_id: form.business_id || null,
      outlet_id: form.outlet_id || null,
      amount: form.amount === '' ? null : Number(form.amount),
      order_items: orderItems,
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
      const params = { report_type: reportType, format, outlet_id: filters.outlet_id || undefined, date_from: filters.date_from || undefined, date_to: filters.date_to || undefined };
      if (effectiveBusinessId) params.business_id = effectiveBusinessId;
      const response = await api.get('/pos-admin/reports-analytics/export', { params, responseType: 'blob' });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${reportType}-report.${format}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      if (err.response?.data instanceof Blob) {
        try { err.response.data = JSON.parse(await err.response.data.text()); }
        catch { /* Keep the original error if the server returned no JSON detail. */ }
      }
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
          {data?.refresh_job && data.refresh_job.status !== 'synced' && (
            <p className="mt-1 text-xs text-amber-700">
              POS refresh: {data.refresh_job.status}
              {data.refresh_job.status === 'retrying' && data.refresh_job.run_after
                ? `; next attempt ${new Date(data.refresh_job.run_after).toLocaleString()}` : ''}
            </p>
          )}
          {data?.refresh_job?.status === 'synced' && data.refresh_job.finished_at && <p className="mt-1 text-xs text-zinc-500">Last confirmed POS refresh: {displayValue(data.refresh_job.finished_at, 'date')}</p>}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={fetchRecords} disabled={loading} className="gap-1.5">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />Refresh
          </Button>
          {!connected && <Button onClick={openCreate} className="gap-1.5 bg-blue-600 hover:bg-blue-700">
            <Plus className="h-3.5 w-3.5" />New Record
          </Button>}
        </div>
      </div>

      {loadError && <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">Records could not refresh: {loadError}{hasSnapshot ? '. Showing the last loaded records.' : ''}</p>}
      {connected && <p className="text-sm text-zinc-500">Create and change operational records in POS. This page shows synchronized records.</p>}
      {!hasSnapshot && <p role="status" className="text-sm text-zinc-500">{loading ? 'Loading records and totals…' : 'Record totals are unavailable.'}</p>}
      <div className={`${hasSnapshot ? 'grid' : 'hidden'} grid-cols-1 md:grid-cols-3 gap-4`}>
        <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Total Records</p><p className="text-2xl font-semibold text-zinc-900 mt-1">{data.summary?.total || 0}</p></CardContent></Card>
        <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><RecordValueSummary summary={data.summary} monetary={monetary} label={resource === 'bills' ? 'Invoice totals' : resource === 'payments' ? 'Recorded payment amounts' : config.amount} pageCount={data.records.length} /></CardContent></Card>
        <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Status Mix</p><div className="flex flex-wrap gap-1.5 mt-2">{(data.summary?.status_counts || []).map(row => <Badge key={row.status} variant="outline" className="text-[11px]">{row.status.replace(/_/g, ' ')}: {row.count}</Badge>)}</div></CardContent></Card>
      </div>

      {resource === 'payments' && hasSnapshot && data.summary?.total === 0 && <p className="rounded border bg-white p-4 text-sm text-zinc-600">No payment transactions are recorded for this view. Invoice sales are separate from payments received. Manage payment collection in POS.</p>}
      {resource === 'payments' && paymentReport && data.summary?.total > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card className="shadow-sm border-zinc-200"><CardContent className="p-5"><p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Payment Method Report</p><div className="flex flex-wrap gap-1.5 mt-2">{paymentReport.by_method?.map(row => <Badge key={`${row.method}:${row.currency}`} variant="outline" className="text-[11px]">{row.method} ({row.currency}): {row.amount == null ? "Unavailable" : formatAmount(row.amount)}</Badge>)}</div></CardContent></Card>
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
                <p className="text-xs text-zinc-500 mt-2">{value.definition}</p>
                {value.amount_by_currency?.map(row => <p key={row.currency} className="text-sm mt-1">{row.currency}: {row.amount == null ? "Unavailable ? invalid source amounts" : formatAmount(row.amount)}</p>)}
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
        {outletError && <p role="alert" className="p-3 text-sm text-amber-800">Outlet choices could not load: {outletError} <Button variant="outline" size="sm" onClick={() => setOutletLookupVersion(value => value + 1)}>Retry outlets</Button></p>}
        <CardContent className="p-4 grid grid-cols-1 md:grid-cols-6 gap-3">
          <Input placeholder="Search records" value={filters.search} onChange={e => setFilters({ ...filters, search: e.target.value })} />
          <Select value={filters.status} onValueChange={status => setFilters({ ...filters, status })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {statuses.map(status => <SelectItem key={status} value={status}>{status.replace(/_/g, ' ')}</SelectItem>)}
            </SelectContent>
          </Select>
          {!selectedBusiness && <Select value={filters.business_id || 'all'} onValueChange={value => setFilters({ ...filters, business_id: value === 'all' ? '' : value, outlet_id: '' })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All businesses</SelectItem>
              {(businesses || []).map(biz => <SelectItem key={biz.id} value={biz.id}>{biz.name}</SelectItem>)}
            </SelectContent>
          </Select>}
          <label className="text-xs text-zinc-500">Outlet<select aria-label="Outlet" className="mt-1 block w-full rounded border p-2 bg-white text-sm text-zinc-900" disabled={!effectiveBusinessId} value={filters.outlet_id} onChange={e => setFilters({ ...filters, outlet_id: e.target.value })}><option value="">All outlets</option>{outletOptions.map(outlet => <option key={outlet.id} value={outlet.id}>{outlet.name}{outlet.status === 'active' ? '' : ` (${outlet.status})`}</option>)}</select></label>
          <label className="text-xs text-zinc-500">From date<Input aria-label="From date" type="date" value={filters.date_from} onChange={e => setFilters({ ...filters, date_from: e.target.value })} /></label>
          <label className="text-xs text-zinc-500">To date<Input aria-label="To date" type="date" value={filters.date_to} onChange={e => setFilters({ ...filters, date_to: e.target.value })} /></label>
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
              <TableRow><TableCell colSpan={8} className="text-center py-12 text-zinc-500">{refreshPending ? 'POS refresh is in progress. No saved records match this view yet.' : 'No records match this view.'}</TableCell></TableRow>
            ) : data.records.map(record => (
              <TableRow key={record.id} className="hover:bg-zinc-50/50">
                <TableCell className="py-3">
                  <div>
                    <p className="font-medium text-zinc-900">{resource === 'bills' ? record.invoice_number || record.metadata?.invoice_number || record.metadata?.invoiceNumber || `Bill ${record.pos_external_id || record.id}` : record.title}</p>
                    <p className="text-xs text-zinc-400">{resource === 'bills' && !record.invoice_number && !record.metadata?.invoice_number && !record.metadata?.invoiceNumber ? 'No invoice number recorded in POS' : record.contact || '-'}</p>
                    {resource === 'sales-orders' && (
                      <p className="text-xs text-zinc-400">
                        {record.order_items?.length || 0} items · Receipt {record.receipt_number || '-'} · Invoice {record.invoice_number || '-'}
                      </p>
                    )}
                    {resource === 'payments' && (
                      <p className="text-xs text-zinc-400">{record.payment_method || record.category || 'manual'} · refund {record.refund_status || 'none'}</p>
                    )}
                    {resource === 'inventory' && record.amount !== null && record.amount !== undefined && record.amount !== '' && Number.isFinite(Number(record.amount)) && Number(record.reorder_level ?? -1) >= 0 && Number(record.amount) <= Number(record.reorder_level) && (
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
                <TableCell className="py-3 text-sm text-zinc-600">{displayValue(record.amount, amountFormat, recordCurrency(record))}{resource === 'customers' && record.metadata?.total_spent_by_currency?.length > 1 && record.metadata.total_spent_by_currency.map(row => <p key={row.currency} className="text-xs text-zinc-500">{row.currency}: {formatAmount(row.amount)}</p>)}</TableCell>
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
                    {!posOwned(record) && <Select value={record.status || ''} onValueChange={status => updateStatus(record, status)}>
                      <SelectTrigger className="h-8 w-28"><SelectValue /></SelectTrigger>
                      <SelectContent>{statuses.map(status => <SelectItem key={status} value={status}>{status.replace(/_/g, ' ')}</SelectItem>)}</SelectContent>
                    </Select>}
                    <Button aria-label={`${posOwned(record) ? 'View' : 'Edit'} ${record.title}`} variant="ghost" size="icon" className="h-8 w-8" onClick={() => openEdit(record)}>{posOwned(record) || resource === 'sales-orders' ? <Eye className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}</Button>
                    {!posOwned(record) && <Button aria-label={`Delete ${record.title}`} variant="ghost" size="icon" className="h-8 w-8 text-red-600 hover:text-red-700 hover:bg-red-50" onClick={() => deleteRecord(record)}><Trash2 className="h-3.5 w-3.5" /></Button>}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-wrap justify-between items-center gap-3 text-sm text-zinc-500"><span>{data.records.length ? `${offset + 1}–${offset + data.records.length}` : '0'} of {data.pagination?.total ?? data.summary?.total ?? 0} records</span><div className="flex gap-2"><Button variant="outline" disabled={loading || offset === 0} onClick={() => setPaging({ scope: filterScope, offset: Math.max(0, offset - 50) })}>Previous</Button><Button variant="outline" disabled={loading || !data.pagination?.has_more} onClick={() => setPaging({ scope: filterScope, offset: offset + 50 })}>Next</Button></div></div>
      <Sheet open={sheetOpen} onOpenChange={open => { if (!open) detailVersion.current += 1; setSheetOpen(open); }}>
        <SheetContent className="sm:max-w-xl overflow-y-auto">
          <SheetHeader><SheetTitle className="font-heading">{editing ? `${posOwned(editing) ? 'View' : 'Edit'} ${config.title}` : `New ${config.title}`}</SheetTitle></SheetHeader>
          {editing && posOwned(editing) ? <div className="space-y-4 mt-6"><p className="text-sm text-zinc-500">This record belongs to POS. Make operational changes there.</p><OperationalRecordDetails record={editing} config={{ columns: [[config.primary, 'title'], [config.category, 'category'], [config.owner, 'owner_name'], [config.contact, 'contact'], [config.amount, 'amount', amountFormat], ['Outlet', 'outlet_id'], ['Created', 'created_at', 'date'], ['Updated', 'updated_at', 'date']] }} /></div> : <form onSubmit={saveRecord} className="space-y-5 mt-6">
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
                  <StructuredRecordEditor label="Order item" text={form.order_items_text} template={{ name: '', quantity: 1, price: 0 }} onChange={value => setForm({ ...form, order_items_text: value })} />
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
                  <Label>Stock by outlet</Label>
                  <StructuredRecordEditor label="Outlet stock" kind="object" template={0} text={form.stock_by_outlet_text} outlets={outletOptions} onChange={value => setForm({ ...form, stock_by_outlet_text: value })} />
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
                  <Label>Recorded order history</Label>
                  <StructuredRecordEditor label="Order" text={form.order_history_text} template={{ order: '', total: 0, date: '' }} fields={{ order: { label: 'Order reference' }, total: { label: 'Total' }, date: { label: 'Date' } }} onChange={value => setForm({ ...form, order_history_text: value })} />
                </div>
                {customerHistory && (
                  <div className="space-y-2">
                    <Label>Matched Order History</Label>
                    <div className="border border-zinc-200 rounded-md divide-y divide-zinc-100 max-h-40 overflow-auto">
                      {[...(customerHistory.orders || []), ...(customerHistory.saved_history || [])].slice(0, historyLimit).map((order, index) => (
                        <div key={order.id || index} className="px-3 py-2 text-xs flex items-center justify-between gap-3">
                          <span className="font-medium text-zinc-700">{order.title || order.order || order.contact || `Order ${index + 1}`}</span>
                          <span className="text-zinc-600">{displayValue(order.amount ?? order.total, 'money', recordCurrency(order))}</span>
                          <span className="text-zinc-400">{formatDate(order.created_at || order.date)}</span>
                        </div>
                      ))}
                    </div>
                    <p className="text-xs text-zinc-500">Showing {Math.min(historyLimit, (customerHistory.orders || []).length + (customerHistory.saved_history || []).length)} of {(customerHistory.pagination?.total ?? (customerHistory.orders || []).length) + (customerHistory.saved_history || []).length} history records</p>
                    {(historyLimit < (customerHistory.orders || []).length + (customerHistory.saved_history || []).length || customerHistory.pagination?.has_more) && <Button type="button" variant="outline" size="sm" disabled={historyLoading} onClick={showMoreHistory}>{historyLoading ? 'Loading history…' : 'Show more history'}</Button>}
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
                  <Label>Reservations</Label>
                  <StructuredRecordEditor label="Reservation" text={form.reservations_text} template={{ name: '', time: '', guests: 1 }} onChange={value => setForm({ ...form, reservations_text: value })} />
                </div>
              </>
            )}
            {resource === 'kitchen-kot' && (
              <>
                <div className="space-y-2"><Label>Chef</Label><Input value={form.chef_name} onChange={e => setForm({ ...form, chef_name: e.target.value })} /></div>
                <div className="space-y-2">
                  <Label>Kitchen ticket items</Label>
                  <StructuredRecordEditor label="Item" text={form.ticket_items_text} template={{ name: '', qty: 1, status: 'preparing' }} fields={{ qty: { label: 'Quantity' } }} onChange={value => setForm({ ...form, ticket_items_text: value })} />
                </div>
                <div className="space-y-2">
                  <Label>Item preparation status</Label>
                  <StructuredRecordEditor label="Item status" kind="object" template="preparing" text={form.item_statuses_text} onChange={value => setForm({ ...form, item_statuses_text: value })} />
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
          </form>}
        </SheetContent>
      </Sheet>
    </div>
  );
}
