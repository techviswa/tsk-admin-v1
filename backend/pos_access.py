"""POS screen permissions belong to a business membership, not a global login."""
from fastapi import HTTPException

SCREEN_PERMISSIONS = {
    'tip-payouts': ('tips',), 'payslips': ('payroll',), 'loyalty-ledger': ('customers',),
    'gift-card-transactions': ('gift_cards',), 'marketing-delivery': ('marketing',),
    'sales-orders': ('billing',), 'orders': ('billing',), 'bills': ('bills',), 'payments': ('billing',),
    'inventory': ('inventory',), 'customers': ('customers',), 'customer-profiles': ('customers',),
    'tables': ('reservations', 'waiter_view'), 'reservations': ('reservations',),
    'kitchen-kot': ('kitchen_view',), 'kitchen-tickets': ('kitchen_view',),
    'reports': ('reports',), 'reports-analytics': ('reports',), 'guest-feedback': ('reports',),
    'staff-shifts': ('staff',), 'attendance': ('attendance',), 'tips': ('tips',), 'payroll': ('payroll',),
    'gift-cards': ('gift_cards',), 'marketing': ('marketing',), 'menu-pricing': ('price_rules',),
    'qr-ordering': ('qr_management',), 'central-kitchen': ('central_kitchen',),
    'table-assistance': ('waiter_view', 'manager_view'), 'products': ('products',),
}
BUSINESS_WIDE = {'payroll', 'payslips', 'marketing', 'marketing-delivery', 'customer-profiles', 'customers', 'loyalty-ledger', 'gift-cards', 'menu-pricing', 'role-permissions', 'staff-shifts'}


def management_role(user, business_id, *, local_owner=False):
    member = membership(user, business_id)
    if member.get('status') in {'inactive', 'disabled'}:
        raise HTTPException(403, 'This business membership is inactive')
    if local_owner:
        return 'business_owner'
    role = member.get('role')
    role = 'business_owner' if role == 'owner' else role
    if role not in {'business_owner', 'manager'}:
        raise HTTPException(403, 'Management authority is required in this business')
    return role


def membership(user, business_id):
    link = (user.get('pos_links') or {}).get(business_id) or {}
    if link:
        if len(user.get('business_ids') or []) == 1:
            return {'role': user.get('role'), 'permissions': user.get('pos_permissions'),
                    'outlet_ids': user.get('pos_assigned_outlet_ids') or [], 'status': user.get('status', 'active'), **link}
        # Legacy links must not inherit permissions from another membership.
        return link
    if len(user.get('business_ids') or []) == 1 and business_id in user.get('business_ids', []):
        return {'role': user.get('role'), 'permissions': user.get('pos_permissions'),
                'outlet_ids': user.get('pos_assigned_outlet_ids') or [], 'status': user.get('status', 'active')}
    return {}


def tenant_read_scope(user, business_id, resource, *, local_owner=False, connected=True):
    member = membership(user, business_id)
    role = str(member.get('role') or '').lower().replace('business_', '')
    if member.get('status') in {'inactive', 'disabled'}:
        raise HTTPException(403, 'This business membership is inactive')
    if local_owner or role == 'owner':
        return None
    required = SCREEN_PERMISSIONS.get(resource, ('settings',))
    permissions = member.get('permissions')
    if not isinstance(permissions, list) or not set(required).intersection(permissions):
        # Standalone local AdminCore records retain owner/manager access; connected
        # business permissions must be explicitly synchronized, including revocations.
        if connected or user.get('role') not in {'business_owner', 'manager'}:
            raise HTTPException(403, {'code': 'POS_PERMISSION_REQUIRED', 'message': 'This business has not granted access to this POS screen.'})
    outlets = member.get('outlet_ids') or []
    if outlets and role != 'manager':
        if resource in BUSINESS_WIDE:
            raise HTTPException(403, 'This view contains business-wide data; outlet-restricted access is insufficient')
        return outlets
    return None
