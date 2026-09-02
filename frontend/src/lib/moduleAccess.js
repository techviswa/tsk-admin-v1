export const POS_RESOURCE_MODULES = {
  'sales-orders': 'pos',
  payments: 'payments',
  bills: 'billing',
  inventory: 'inventory',
  customers: 'customers',
  tables: 'tables',
  reservations: 'reservations',
  'kitchen-kot': 'kitchen',
  'reports-analytics': 'analytics',
  'taxes-charges': 'taxes_charges',
  'discounts-coupons': 'discounts_coupons',
  'staff-shifts': 'staff',
  'suppliers-purchasing': 'suppliers_purchasing',
  expenses: 'expenses',
  'hardware-printers': 'hardware_printers',
  'role-permissions': 'users_roles',
  notifications: 'notifications',
  'import-export': 'import_export',
  'integrations-webhooks': 'integrations',
  'audit-security': 'audit_security',
};

export const ROUTE_MODULES = {
  '/outlets': 'businesses',
  '/products': 'products',
  '/users': 'users_roles',
  '/settings': 'modules',
  '/feature-flags': 'feature_flags',
  '/audit-logs': 'audit_security',
  '/integrations': 'integrations',
  '/pos-bridge': 'pos_bridge',
  '/subscriptions': 'subscriptions',
};

export function enabledModuleSet(modules = []) {
  return new Set((modules || []).filter(mod => mod.enabled).map(mod => mod.slug));
}

export function isModuleEnabled(moduleSet, moduleSlug) {
  if (!moduleSlug) return true;
  return moduleSet.has(moduleSlug);
}
