#!/usr/bin/env python3
"""
Backend API Testing for AdminCore Multi-tenant Business Admin Panel
Tests all API endpoints for authentication, businesses, outlets, modules, users, settings, etc.
"""

import requests
import sys
import json
from datetime import datetime
from typing import Dict, Any, Optional

class AdminCoreAPITester:
    def __init__(self, base_url: str = "https://ecosystem-admin-core.preview.emergentagent.com"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})
        self.tests_run = 0
        self.tests_passed = 0
        self.current_user = None
        self.businesses = []
        self.test_business_id = None
        
    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {message}")
        
    def run_test(self, name: str, method: str, endpoint: str, expected_status: int, 
                 data: Optional[Dict] = None, params: Optional[Dict] = None) -> tuple[bool, Dict]:
        """Run a single API test"""
        url = f"{self.base_url}/api/{endpoint.lstrip('/')}"
        
        self.tests_run += 1
        self.log(f"Testing {name}...")
        
        try:
            if method == 'GET':
                response = self.session.get(url, params=params)
            elif method == 'POST':
                response = self.session.post(url, json=data, params=params)
            elif method == 'PUT':
                response = self.session.put(url, json=data, params=params)
            elif method == 'DELETE':
                response = self.session.delete(url, params=params)
            else:
                raise ValueError(f"Unsupported method: {method}")
                
            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                self.log(f"✅ {name} - Status: {response.status_code}")
            else:
                self.log(f"❌ {name} - Expected {expected_status}, got {response.status_code}", "ERROR")
                if response.text:
                    self.log(f"Response: {response.text[:200]}", "ERROR")
                    
            try:
                response_data = response.json() if response.text else {}
            except:
                response_data = {"raw_response": response.text}
                
            return success, response_data
            
        except Exception as e:
            self.log(f"❌ {name} - Error: {str(e)}", "ERROR")
            return False, {"error": str(e)}

    def test_auth_login(self, email: str, password: str) -> bool:
        """Test login and store session"""
        success, response = self.run_test(
            "Auth Login",
            "POST", 
            "auth/login",
            200,
            data={"email": email, "password": password}
        )
        
        if success:
            self.current_user = response
            self.log(f"Logged in as: {response.get('name')} ({response.get('role')})")
            return True
        return False

    def test_auth_me(self) -> bool:
        """Test get current user"""
        success, response = self.run_test("Auth Me", "GET", "auth/me", 200)
        return success

    def test_auth_register(self) -> bool:
        """Test user registration"""
        test_email = f"test_{datetime.now().strftime('%H%M%S')}@test.com"
        success, response = self.run_test(
            "Auth Register",
            "POST",
            "auth/register", 
            200,
            data={"email": test_email, "password": "testpass123", "name": "Test User"}
        )
        return success

    def test_businesses_list(self) -> bool:
        """Test list businesses"""
        success, response = self.run_test("List Businesses", "GET", "businesses", 200)
        if success:
            self.businesses = response
            self.log(f"Found {len(self.businesses)} businesses")
            if self.businesses:
                self.test_business_id = self.businesses[0]['id']
                self.log(f"Using business: {self.businesses[0]['name']} (ID: {self.test_business_id})")
        return success

    def test_business_create(self) -> bool:
        """Test create business"""
        test_name = f"Test Business {datetime.now().strftime('%H%M%S')}"
        success, response = self.run_test(
            "Create Business",
            "POST",
            "businesses",
            200,
            data={"name": test_name, "type": "restaurant", "plan": "starter"}
        )
        return success

    def test_outlets_list(self) -> bool:
        """Test list outlets for a business"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "List Outlets",
            "GET", 
            f"outlets/business/{self.test_business_id}",
            200
        )
        if success:
            self.log(f"Found {len(response)} outlets for business")
        return success

    def test_outlet_create(self) -> bool:
        """Test create outlet"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "Create Outlet",
            "POST",
            f"outlets/business/{self.test_business_id}",
            200,
            data={"name": "Test Outlet", "address": "123 Test St", "phone": "+1-555-TEST"}
        )
        return success

    def test_modules_list(self) -> bool:
        """Test list all modules"""
        success, response = self.run_test("List Modules", "GET", "modules", 200)
        if success:
            self.log(f"Found {len(response)} system modules")
        return success

    def test_business_modules_list(self) -> bool:
        """Test list business modules"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "List Business Modules",
            "GET",
            f"modules/business/{self.test_business_id}",
            200
        )
        if success:
            self.log(f"Found {len(response)} business modules")
        return success

    def test_module_toggle(self) -> bool:
        """Test toggle business module"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "Toggle Module",
            "PUT",
            f"modules/business/{self.test_business_id}/pos",
            200,
            data={"enabled": True, "config": {}}
        )
        return success

    def test_users_list(self) -> bool:
        """Test list users"""
        success, response = self.run_test(
            "List Users",
            "GET",
            "users",
            200,
            params={"business_id": self.test_business_id} if self.test_business_id else None
        )
        if success:
            self.log(f"Found {len(response)} users")
        return success

    def test_user_create(self) -> bool:
        """Test create user"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        test_email = f"testuser_{datetime.now().strftime('%H%M%S')}@test.com"
        success, response = self.run_test(
            "Create User",
            "POST",
            "users",
            200,
            data={
                "email": test_email,
                "name": "Test User",
                "password": "testpass123",
                "role": "staff",
                "business_ids": [self.test_business_id]
            }
        )
        return success

    def test_settings_list(self) -> bool:
        """Test list settings"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "List Settings",
            "GET",
            f"settings/business/{self.test_business_id}",
            200
        )
        if success:
            self.log(f"Found {len(response)} settings")
        return success

    def test_setting_update(self) -> bool:
        """Test update setting"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "Update Setting",
            "PUT",
            f"settings/business/{self.test_business_id}/timezone",
            200,
            data={"value": "America/Los_Angeles"}
        )
        return success

    def test_feature_flags_list(self) -> bool:
        """Test list feature flags"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "List Feature Flags",
            "GET",
            f"feature-flags/business/{self.test_business_id}",
            200
        )
        if success:
            self.log(f"Found {len(response)} feature flags")
        return success

    def test_feature_flag_create(self) -> bool:
        """Test create feature flag"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "Create Feature Flag",
            "POST",
            f"feature-flags/business/{self.test_business_id}",
            200,
            data={
                "key": f"test_flag_{datetime.now().strftime('%H%M%S')}",
                "name": "Test Flag",
                "description": "Test feature flag",
                "enabled": True
            }
        )
        return success

    def test_audit_logs_list(self) -> bool:
        """Test list audit logs"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "List Audit Logs",
            "GET",
            f"audit-logs/business/{self.test_business_id}",
            200
        )
        if success:
            logs = response.get('logs', [])
            self.log(f"Found {len(logs)} audit logs")
        return success

    def test_integrations_list(self) -> bool:
        """Test list integrations"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "List Integrations",
            "GET",
            f"integrations/business/{self.test_business_id}",
            200
        )
        if success:
            self.log(f"Found {len(response)} integrations")
        return success

    def test_integration_create(self) -> bool:
        """Test create integration"""
        if not self.test_business_id:
            self.log("No test business ID available", "ERROR")
            return False
            
        success, response = self.run_test(
            "Create Integration",
            "POST",
            f"integrations/business/{self.test_business_id}",
            200,
            data={
                "slug": f"test_integration_{datetime.now().strftime('%H%M%S')}",
                "name": "Test Integration",
                "type": "webhook",
                "config": {}
            }
        )
        return success

    def test_dashboard_stats(self) -> bool:
        """Test dashboard stats"""
        success, response = self.run_test(
            "Dashboard Stats",
            "GET",
            "dashboard/stats",
            200,
            params={"business_id": self.test_business_id} if self.test_business_id else None
        )
        if success:
            stats = response
            self.log(f"Dashboard stats: {stats.get('total_businesses', 0)} businesses, "
                    f"{stats.get('total_outlets', 0)} outlets, "
                    f"{stats.get('active_modules', 0)} modules, "
                    f"{stats.get('total_users', 0)} users")
        return success

    def test_auth_logout(self) -> bool:
        """Test logout"""
        success, response = self.run_test("Auth Logout", "POST", "auth/logout", 200)
        if success:
            self.current_user = None
        return success

    def test_plans_api(self):
        """Test Plans API endpoints"""
        self.log("Testing Plans API...")
        
        # Test GET /api/plans - should return 4 seeded plans
        success, plans_data = self.run_test(
            "Get Plans List",
            "GET", 
            "plans",
            200
        )
        
        if success:
            plans = plans_data if isinstance(plans_data, list) else []
            self.log(f"Found {len(plans)} plans")
            expected_plans = ["free", "starter", "pro", "enterprise"]
            found_slugs = [p.get('slug') for p in plans]
            
            for expected_slug in expected_plans:
                if expected_slug in found_slugs:
                    self.log(f"✅ Found {expected_slug} plan")
                else:
                    self.log(f"❌ Missing {expected_slug} plan", "ERROR")
        
        # Test POST /api/plans - create new plan
        test_plan_data = {
            "name": "Test Plan",
            "slug": "test-plan",
            "description": "Test plan for API testing",
            "trial_days": 7,
            "pricing": {"monthly": 15, "yearly": 150, "currency": "USD"},
            "limits": {"max_outlets": 2, "max_users": 5, "max_modules": 4, "max_integrations": 1},
            "included_modules": ["pos", "billing"],
            "features": {"white_label": False, "api_access": True},
            "sort_order": 10
        }
        
        success, created_plan = self.run_test(
            "Create New Plan",
            "POST",
            "plans",
            200,
            data=test_plan_data
        )
        
        created_plan_id = None
        if success and 'id' in created_plan:
            created_plan_id = created_plan['id']
            self.log(f"Created plan with ID: {created_plan_id}")
        
        # Test PUT /api/plans/{id} - update plan
        if created_plan_id:
            update_data = {
                "name": "Updated Test Plan",
                "description": "Updated description",
                "pricing": {"monthly": 20, "yearly": 200, "currency": "USD"}
            }
            
            self.run_test(
                "Update Plan",
                "PUT",
                f"plans/{created_plan_id}",
                200,
                data=update_data
            )
        
        # Test DELETE /api/plans/{id} - delete plan
        if created_plan_id:
            self.run_test(
                "Delete Plan",
                "DELETE",
                f"plans/{created_plan_id}",
                200
            )

    def test_subscriptions_api(self):
        """Test Subscriptions API endpoints"""
        self.log("Testing Subscriptions API...")
        
        # Test GET /api/subscriptions - should return 3 subscriptions
        success, subs_data = self.run_test(
            "Get Subscriptions List",
            "GET",
            "subscriptions",
            200
        )
        
        subscription_id = None
        if success:
            subs = subs_data if isinstance(subs_data, list) else []
            self.log(f"Found {len(subs)} subscriptions")
            
            expected_businesses = ["sunrise-restaurant", "urban-wellness-cafe", "metro-retail-hub"]
            found_businesses = [s.get('business_slug') for s in subs]
            
            for expected_biz in expected_businesses:
                if expected_biz in found_businesses:
                    self.log(f"✅ Found subscription for {expected_biz}")
                else:
                    self.log(f"❌ Missing subscription for {expected_biz}", "ERROR")
            
            if subs:
                subscription_id = subs[0].get('id')
                self.log(f"Using subscription ID for testing: {subscription_id}")
        
        # Test PUT /api/subscriptions/{id} - update subscription
        if subscription_id:
            update_data = {
                "billing_cycle": "yearly",
                "status": "active"
            }
            
            self.run_test(
                "Update Subscription",
                "PUT",
                f"subscriptions/{subscription_id}",
                200,
                data=update_data
            )
        
        # Test POST /api/subscriptions/{id}/cancel - cancel subscription
        if subscription_id:
            self.run_test(
                "Cancel Subscription",
                "POST",
                f"subscriptions/{subscription_id}/cancel",
                200
            )

    def test_entitlements_api(self):
        """Test Entitlements API endpoint"""
        self.log("Testing Entitlements API...")
        
        if self.businesses:
            business_id = self.businesses[0].get('id')
            self.log(f"Testing entitlements for business: {business_id}")
            
            success, entitlements = self.run_test(
                "Get Business Entitlements",
                "GET",
                f"businesses/{business_id}/entitlements",
                200
            )
            
            if success:
                self.log("✅ Entitlements response received")
                if 'plan' in entitlements:
                    self.log(f"Plan: {entitlements.get('plan', {}).get('name', 'Unknown')}")
                if 'usage' in entitlements:
                    usage = entitlements.get('usage', {})
                    self.log(f"Usage - Outlets: {usage.get('outlets', 0)}, Users: {usage.get('users', 0)}")
                if 'limits' in entitlements:
                    limits = entitlements.get('limits', {})
                    self.log(f"Limits - Max Outlets: {limits.get('max_outlets', 0)}, Max Users: {limits.get('max_users', 0)}")
    def run_all_tests(self) -> int:
        """Run all API tests"""
        self.log("Starting AdminCore API Testing...")
        self.log(f"Base URL: {self.base_url}")
        
        # Test authentication
        if not self.test_auth_login("admin@admin.com", "admin123"):
            self.log("Login failed, stopping tests", "ERROR")
            return 1
            
        # Test auth endpoints
        self.test_auth_me()
        
        # Test business management
        self.test_businesses_list()
        self.test_business_create()
        
        # Test outlets
        self.test_outlets_list()
        self.test_outlet_create()
        
        # Test modules
        self.test_modules_list()
        self.test_business_modules_list()
        self.test_module_toggle()
        
        # Test users
        self.test_users_list()
        self.test_user_create()
        
        # Test settings
        self.test_settings_list()
        self.test_setting_update()
        
        # Test feature flags
        self.test_feature_flags_list()
        self.test_feature_flag_create()
        
        # Test audit logs
        self.test_audit_logs_list()
        
        # Test integrations
        self.test_integrations_list()
        self.test_integration_create()
        
        # Test dashboard
        self.test_dashboard_stats()
        
        # Test new subscription and plan management features
        self.test_plans_api()
        self.test_subscriptions_api()
        self.test_entitlements_api()
        
        # Test registration (new session)
        self.test_auth_register()
        
        # Test logout
        self.test_auth_logout()
        
        # Print results
        self.log("=" * 50)
        self.log(f"Tests completed: {self.tests_passed}/{self.tests_run} passed")
        success_rate = (self.tests_passed / self.tests_run * 100) if self.tests_run > 0 else 0
        self.log(f"Success rate: {success_rate:.1f}%")
        
        return 0 if self.tests_passed == self.tests_run else 1

def main():
    tester = AdminCoreAPITester()
    return tester.run_all_tests()

if __name__ == "__main__":
    sys.exit(main())