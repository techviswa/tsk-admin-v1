import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate, useParams } from "react-router-dom";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { Toaster } from "@/components/ui/sonner";
import { Lock } from "lucide-react";
import LoginPage from "@/pages/LoginPage";
import DashboardLayout, { useBusiness } from "@/components/layout/DashboardLayout";
import ControlCenterPage from "@/pages/ControlCenterPage";
import DashboardPage from "@/pages/DashboardPage";
import ClientsPage from "@/pages/ClientsPage";
import BusinessesPage from "@/pages/BusinessesPage";
import OutletsPage from "@/pages/OutletsPage";
import ProductsPage from "@/pages/ProductsPage";
import ModulesPage from "@/pages/ModulesPage";
import UsersPage from "@/pages/UsersPage";
import SettingsPage from "@/pages/SettingsPage";
import FeatureFlagsPage from "@/pages/FeatureFlagsPage";
import AuditLogsPage from "@/pages/AuditLogsPage";
import IntegrationsPage from "@/pages/IntegrationsPage";
import PlansPage from "@/pages/PlansPage";
import SubscriptionsPage from "@/pages/SubscriptionsPage";
import POSAdminPage from "@/pages/POSAdminPage";
import POSBridgePage from "@/pages/POSBridgePage";
import { POS_RESOURCE_MODULES, ROUTE_MODULES } from "@/lib/moduleAccess";

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen bg-zinc-50 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-zinc-500">Loading...</span>
        </div>
      </div>
    );
  }
  if (user === false) return <Navigate to="/login" replace />;
  return children;
}

function LockedModule({ moduleSlug }) {
  return (
    <div className="min-h-[60vh] flex items-center justify-center" data-testid="module-locked-page">
      <div className="text-center max-w-md">
        <div className="mx-auto mb-4 h-12 w-12 rounded-lg bg-zinc-100 text-zinc-500 flex items-center justify-center">
          <Lock className="h-5 w-5" />
        </div>
        <h2 className="text-xl font-heading font-semibold text-zinc-950">Module Disabled</h2>
        <p className="text-sm text-zinc-500 mt-2">Enable the {moduleSlug} module for this business to access this page.</p>
      </div>
    </div>
  );
}

function ModuleGate({ moduleSlug, children }) {
  const { user } = useAuth();
  const { selectedBusiness, isModuleEnabled, modulesLoading } = useBusiness();
  if (selectedBusiness && modulesLoading) {
    return (
      <div className="min-h-[50vh] flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }
  if (selectedBusiness && user?.role !== 'platform_admin' && !isModuleEnabled(moduleSlug)) {
    return <LockedModule moduleSlug={moduleSlug} />;
  }
  return children;
}

function POSAdminGate() {
  const { resource } = useParams();
  return (
    <ModuleGate moduleSlug={POS_RESOURCE_MODULES[resource]}>
      <POSAdminPage />
    </ModuleGate>
  );
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<ProtectedRoute><DashboardLayout /></ProtectedRoute>}>
            <Route index element={<DashboardPage />} />
            <Route path="control-center" element={<ControlCenterPage />} />
            <Route path="clients" element={<ClientsPage />} />
            <Route path="businesses" element={<BusinessesPage />} />
            <Route path="outlets" element={<ModuleGate moduleSlug={ROUTE_MODULES['/outlets']}><OutletsPage /></ModuleGate>} />
            <Route path="products" element={<ModuleGate moduleSlug={ROUTE_MODULES['/products']}><ProductsPage /></ModuleGate>} />
            <Route path="modules" element={<ModulesPage />} />
            <Route path="users" element={<ModuleGate moduleSlug={ROUTE_MODULES['/users']}><UsersPage /></ModuleGate>} />
            <Route path="settings" element={<ModuleGate moduleSlug={ROUTE_MODULES['/settings']}><SettingsPage /></ModuleGate>} />
            <Route path="feature-flags" element={<ModuleGate moduleSlug={ROUTE_MODULES['/feature-flags']}><FeatureFlagsPage /></ModuleGate>} />
            <Route path="audit-logs" element={<ModuleGate moduleSlug={ROUTE_MODULES['/audit-logs']}><AuditLogsPage /></ModuleGate>} />
            <Route path="integrations" element={<ModuleGate moduleSlug={ROUTE_MODULES['/integrations']}><IntegrationsPage /></ModuleGate>} />
            <Route path="pos-bridge" element={<ModuleGate moduleSlug={ROUTE_MODULES['/pos-bridge']}><POSBridgePage /></ModuleGate>} />
            <Route path="plans" element={<PlansPage />} />
            <Route path="subscriptions" element={<ModuleGate moduleSlug={ROUTE_MODULES['/subscriptions']}><SubscriptionsPage /></ModuleGate>} />
            <Route path="pos-admin/:resource" element={<POSAdminGate />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster position="top-right" richColors />
    </AuthProvider>
  );
}

export default App;
