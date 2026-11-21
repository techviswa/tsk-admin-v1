import unittest
from fastapi import HTTPException
from pos_access import tenant_read_scope, management_role


class PosPermissionTests(unittest.TestCase):
    def test_management_authority_is_per_business_and_inactive_membership_wins(self):
        user = {'role': 'business_owner', 'business_ids': ['a', 'b'], 'pos_links': {
            'a': {'role': 'business_owner', 'status': 'active'}, 'b': {'role': 'staff', 'status': 'active'}}}
        self.assertEqual(management_role(user, 'a'), 'business_owner')
        with self.assertRaises(HTTPException):
            management_role(user, 'b')
        user['pos_links']['a']['status'] = 'inactive'
        with self.assertRaises(HTTPException):
            management_role(user, 'a', local_owner=True)

    def test_same_login_does_not_inherit_another_business_permissions(self):
        user = {'role': 'business_owner', 'business_ids': ['a', 'b'], 'pos_permissions': ['payroll'], 'pos_links': {
            'a': {'role': 'business_owner', 'permissions': ['payroll']},
            'b': {'role': 'staff', 'permissions': ['attendance'], 'outlet_ids': ['outlet-b']}}}
        self.assertIsNone(tenant_read_scope(user, 'a', 'payroll'))
        self.assertEqual(tenant_read_scope(user, 'b', 'attendance'), ['outlet-b'])
        with self.assertRaises(HTTPException):
            tenant_read_scope(user, 'b', 'payroll')

    def test_explicit_empty_permissions_never_restore_manager_defaults(self):
        user = {'role': 'manager', 'business_ids': ['b'], 'pos_permissions': ['payroll'],
                'pos_links': {'b': {'role': 'manager', 'permissions': []}}}
        with self.assertRaises(HTTPException):
            tenant_read_scope(user, 'b', 'payroll')

    def test_outlet_permission_cannot_read_business_wide_financial_data(self):
        user = {'business_ids': ['b'], 'pos_links': {'b': {'role': 'staff', 'permissions': ['payroll'], 'outlet_ids': ['o']}}}
        with self.assertRaises(HTTPException):
            tenant_read_scope(user, 'b', 'payroll')
