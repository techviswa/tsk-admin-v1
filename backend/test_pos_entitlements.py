import unittest
from pos_entitlements import pos_entitlement_payload


class PosEntitlementTests(unittest.TestCase):
    def test_starter_kitchen_grant_is_not_lost_to_pos_plan_defaults(self):
        payload = pos_entitlement_payload({"plan": {"slug": "starter"},
            "features": {"kot.basic": True, "inventory.basic": True, "tables.basic": True},
            "limits": {"users.max": 8}})
        self.assertEqual(payload["plan"], "starter")
        self.assertIn("kot", payload["enabled_features"])
        self.assertEqual(payload["limits"]["staff"], 8)

    def test_disabled_features_do_not_grant_access(self):
        payload = pos_entitlement_payload({"features": {"kot.basic": False}})
        self.assertEqual(payload["enabled_features"], [])

    def test_business_plan_and_unlimited_limits_translate(self):
        payload = pos_entitlement_payload({"plan": {"code": "BUSINESS"},
            "limits": {"outlets.max": "unlimited"}})
        self.assertEqual(payload["plan"], "enterprise")
        self.assertIsNone(payload["limits"]["outlets"])

    def test_explicit_module_switch_is_respected_without_changing_plan_grant(self):
        entitlement = {"features": {"kot.basic": True}, "module_overrides": {"kitchen": False}}
        self.assertNotIn("kot", pos_entitlement_payload(entitlement)["enabled_features"])
        self.assertTrue(entitlement["features"]["kot.basic"])
