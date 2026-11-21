// Column values come from the source snapshot, not separately editable AdminCore records.
export const POS_OPERATIONS = {
  'tip-payouts': { title: 'Tip Payouts', module: 'staff', columns: [['Staff', 'userName'], ['Amount', 'amount', 'money'], ['Period from', 'periodFrom', 'date'], ['Period to', 'periodTo', 'date'], ['Method', 'method']] },
  payslips: { title: 'Payslips', module: 'staff', columns: [['Staff', 'userName'], ['Run', 'runId'], ['Gross', 'gross', 'money'], ['Deductions', 'totalDeductions', 'money'], ['Net pay', 'netPay', 'money'], ['Paid', 'paidAt', 'date']] },
  'loyalty-ledger': { title: 'Loyalty Ledger', module: 'customers', columns: [['Customer ID', 'customerId'], ['Type', 'type'], ['Points', 'points'], ['Balance', 'balanceAfter'], ['Bill ID', 'billId']] },
  'gift-card-transactions': { title: 'Gift Card Transactions', module: 'customers', columns: [['Card ID', 'giftCardId'], ['Type', 'type'], ['Amount', 'amount', 'money'], ['Balance', 'balanceAfter', 'money'], ['Bill ID', 'billId']] },
  'marketing-delivery': { title: 'Marketing Delivery', module: 'customers', columns: [['Campaign ID', 'campaignId'], ['Customer ID', 'customerId'], ['Channel', 'channel'], ['Attempts', 'attempts'], ['Sent', 'sentAt', 'date'], ['Delivered', 'deliveredAt', 'date']] },
  attendance: { title: 'Attendance', module: 'staff', columns: [['Staff', 'userName'], ['Clock in', 'clockInAt', 'date'], ['Clock out', 'clockOutAt', 'date'], ['Source', 'source']] },
  tips: { title: 'Tips', module: 'staff', columns: [['Recipient', 'recipientName'], ['Amount', 'amount', 'money'], ['Source', 'source'], ['Method', 'method']] },
  payroll: { title: 'Payroll', module: 'staff', columns: [['Run', 'label'], ['Period from', 'periodFrom', 'date'], ['Period to', 'periodTo', 'date'], ['Payslips', 'payslip_count']] },
  'customer-profiles': { title: 'Customer Profiles & Loyalty', module: 'customers', columns: [['Name', 'name'], ['Phone', 'phone'], ['Visits', 'visitCount'], ['Loyalty points', 'loyaltyPoints'], ['Total spend', 'totalSpent', 'money']] },
  'gift-cards': { title: 'Gift Cards', module: 'customers', columns: [['Card', 'masked_code'], ['Recipient', 'recipientName'], ['Initial value', 'initialValue', 'money'], ['Balance', 'balance', 'money'], ['Expires', 'expiresAt', 'date']] },
  marketing: { title: 'Marketing', module: 'customers', columns: [['Campaign', 'name'], ['Channel', 'channel'], ['Type', 'kind'], ['Scheduled', 'scheduledAt', 'date']] },
  'menu-pricing': { title: 'Menu Pricing / Happy Hours', module: 'products', columns: [['Rule', 'name'], ['Discount', 'discountType'], ['Value', 'value'], ['Time zone', 'timezone']] },
  'guest-feedback': { title: 'Guest Feedback', module: 'analytics', columns: [['Guest', 'customerName'], ['Rating', 'rating'], ['Comment', 'comment'], ['Received', 'createdAt', 'date']] },
  'table-assistance': { title: 'Table Assistance', module: 'tables', columns: [['Table', 'table_name'], ['Request', 'kind'], ['Assigned to', 'assignee_name'], ['Requested', 'created_at', 'date']] },
  'qr-ordering': { title: 'QR Ordering', module: 'tables', columns: [['Record', 'title'], ['Type', 'resource_type'], ['Table', 'table_name'], ['Opened', 'opened_at', 'date'], ['Scans', 'scan_count']] },
  'central-kitchen': { title: 'Central Kitchen', module: 'inventory', columns: [['Record', 'title'], ['Type', 'resource_type'], ['Outlet', 'outlet_name'], ['Amount', 'amount', 'money']] },
};

export function sourceValue(record, key) {
  return record.metadata?.[key] ?? record[key];
}

export const fieldLabel = key => key.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, letter => letter.toUpperCase());
export const entityLabel = key => String(key || '').startsWith('pos_bridge:') ? `POS: ${POS_OPERATIONS[key.slice(11)]?.title || fieldLabel(key.slice(11).replace(/-/g, ' '))}` : fieldLabel(key || 'activity');
export const recordCurrency = record => record?.metadata?.currency || record?.currency || null;

export function displayValue(value, format, currency) {
  if (value === null || value === undefined || value === '') return '—';
  if (format === 'date') {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }
  if (format === 'money') {
    if (typeof value === 'boolean' || !Number.isFinite(Number(value))) return 'Amount unavailable';
    const amount = Number(value).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return `${amount} ${currency && /^[A-Z]{3}$/.test(currency) ? currency : '(currency not provided)'}`;
  }
  if (format === 'quantity') return Number.isFinite(Number(value)) ? Number(value).toLocaleString('en-IN', { maximumFractionDigits: 6 }) : 'Quantity unavailable';
  if (Array.isArray(value)) return value.map(item => displayValue(item)).join(', ');
  if (typeof value === 'object') return Object.entries(value).filter(([key]) => !/token|secret|password|session_key|bank|pin/i.test(key)).map(([key, item]) => `${key}: ${displayValue(item)}`).join('; ');
  return typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);
}
