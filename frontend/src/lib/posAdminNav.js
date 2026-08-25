import {
  Bell, Boxes, CalendarClock, ChefHat, CreditCard, Download, FileSpreadsheet,
  HandCoins, History, Plug, Printer, Receipt, ShieldCheck, ShoppingBag, Table2,
  Tags, Truck, UserRound, Users, CalendarDays
} from 'lucide-react';

export const POS_ADMIN_NAV = [
  { priority: 1, path: '/pos-admin/sales-orders', label: 'POS Orders', icon: ShoppingBag, moduleSlug: 'pos' },
  { priority: 2, path: '/pos-admin/payments', label: 'Payments', icon: CreditCard, moduleSlug: 'payments' },
  { priority: 3, path: '/pos-admin/bills', label: 'Bills', icon: Receipt, moduleSlug: 'billing' },
  { priority: 4, path: '/pos-admin/inventory', label: 'Inventory', icon: Boxes, moduleSlug: 'inventory' },
  { priority: 5, path: '/pos-admin/customers', label: 'Customers CRM', icon: Users, moduleSlug: 'customers' },
  { priority: 6, path: '/pos-admin/tables', label: 'Tables', icon: Table2, moduleSlug: 'tables' },
  { priority: 7, path: '/pos-admin/reservations', label: 'Reservations', icon: CalendarDays, moduleSlug: 'reservations' },
  { priority: 8, path: '/pos-admin/kitchen-kot', label: 'Kitchen KOT', icon: ChefHat, moduleSlug: 'kitchen' },
  { priority: 9, path: '/pos-admin/reports-analytics', label: 'Reports', icon: FileSpreadsheet, moduleSlug: 'analytics' },
  { priority: 10, path: '/pos-admin/taxes-charges', label: 'Taxes', icon: Receipt, moduleSlug: 'taxes_charges' },
  { priority: 11, path: '/pos-admin/discounts-coupons', label: 'Discounts', icon: Tags, moduleSlug: 'discounts_coupons' },
  { priority: 12, path: '/pos-admin/staff-shifts', label: 'Staff Shifts', icon: UserRound, moduleSlug: 'staff' },
  { priority: 13, path: '/pos-admin/suppliers-purchasing', label: 'Suppliers', icon: Truck, moduleSlug: 'suppliers_purchasing' },
  { priority: 14, path: '/pos-admin/expenses', label: 'Expenses', icon: HandCoins, moduleSlug: 'expenses' },
  { priority: 15, path: '/pos-admin/hardware-printers', label: 'Hardware', icon: Printer, moduleSlug: 'hardware_printers' },
  { priority: 16, path: '/pos-admin/role-permissions', label: 'Permissions', icon: ShieldCheck, moduleSlug: 'users_roles' },
  { priority: 17, path: '/pos-admin/notifications', label: 'Notifications', icon: Bell, moduleSlug: 'notifications' },
  { priority: 18, path: '/pos-admin/import-export', label: 'Import Export', icon: Download, moduleSlug: 'import_export' },
  { priority: 19, path: '/pos-admin/integrations-webhooks', label: 'Webhooks', icon: Plug, moduleSlug: 'integrations' },
  { priority: 20, path: '/pos-admin/audit-security', label: 'Audit Security', icon: History, moduleSlug: 'audit_security' },
];
