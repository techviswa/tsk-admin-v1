// UI hints only. The backend authorises every request; hiding a control here never grants or removes access.

export const PLATFORM_ROLE_LABELS = {
  platform_admin: 'Super admin',
  platform_operations: 'Operations',
  platform_finance: 'Finance',
  platform_security: 'Security',
  platform_auditor: 'Auditor (read-only)',
};

export const isPlatformStaff = (user) =>
  Array.isArray(user?.platform_permissions) && user.platform_permissions.length > 0;

export const hasPlatformPermission = (user, permission) => {
  const granted = user?.platform_permissions || [];
  return granted.includes('*') || granted.includes(permission);
};

// Mirrors backend platform_rbac.ASSIGNABLE_ROLES: nobody can grant a role above their own.
export const assignableRoles = (user) => {
  if (user?.role === 'platform_admin') {
    return ['platform_admin', 'platform_operations', 'platform_finance', 'platform_security', 'platform_auditor',
      'business_owner', 'manager', 'staff', 'support_admin'];
  }
  if (user?.role === 'business_owner') return ['business_owner', 'manager', 'staff'];
  if (user?.role === 'manager') return ['staff'];
  return [];
};
