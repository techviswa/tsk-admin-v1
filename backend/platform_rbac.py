"""Internal (Taskoora staff) RBAC for the AdminCore control plane.

Two different axes exist and must never be conflated:

* Platform roles  - Taskoora employees who administer the SaaS itself.
* Tenant roles    - people who belong to a customer business (owner/manager/staff).

Platform permissions are *only* granted through PLATFORM_ROLE_PERMISSIONS below.
Tenant roles never receive a platform permission, whatever business they belong to.
"""

SUPER_ADMIN = "platform_admin"

PLATFORM_ROLE_PERMISSIONS = {
    SUPER_ADMIN: {"*"},
    "platform_operations": {
        "pos.read", "pos.customers.read", "pos.marketing.read",
        "businesses.read", "businesses.write", "businesses.suspend",
        "modules.write", "provisioning.run", "users.read", "plans.read",
        "subscriptions.read", "audit.read",
    },
    "platform_finance": {
        "pos.read", "pos.payroll.read", "pos.tips.read", "pos.gift_cards.read",
        "businesses.read", "plans.read", "plans.write", "subscriptions.read",
        "subscriptions.write", "entitlements.write", "audit.read",
    },
    "platform_security": {
        "businesses.read", "users.read", "users.write", "sessions.revoke",
        "audit.read", "subscriptions.read", "plans.read",
    },
    "platform_auditor": {
        "pos.read",
        "businesses.read", "users.read", "plans.read", "subscriptions.read", "audit.read",
    },
}

PLATFORM_ROLES = frozenset(PLATFORM_ROLE_PERMISSIONS)

# support_admin is a tenant-scoped support login (it carries business_ids); it has no platform permissions.
TENANT_ROLES = ("business_owner", "manager", "staff", "support_admin")
ALL_ROLES = tuple(PLATFORM_ROLE_PERMISSIONS) + TENANT_ROLES

# Which roles an actor may hand out. Nobody can grant a role more powerful than their own.
ASSIGNABLE_ROLES = {
    SUPER_ADMIN: set(ALL_ROLES),
    "business_owner": {"business_owner", "manager", "staff"},
    "manager": {"staff"},
}


def is_platform_staff(user) -> bool:
    return (user or {}).get("role") in PLATFORM_ROLES


def has_platform_permission(user, permission: str) -> bool:
    permissions = PLATFORM_ROLE_PERMISSIONS.get((user or {}).get("role"), set())
    return "*" in permissions or permission in permissions


def platform_permissions_for(user) -> list:
    return sorted(PLATFORM_ROLE_PERMISSIONS.get((user or {}).get("role"), set()))


def can_assign_role(actor, target_role: str) -> bool:
    return target_role in ASSIGNABLE_ROLES.get((actor or {}).get("role"), set())
