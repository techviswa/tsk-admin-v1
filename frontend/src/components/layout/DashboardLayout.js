import { useState, createContext, useContext, useEffect, useCallback } from 'react';
import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { Sheet, SheetContent, SheetTrigger } from '@/components/ui/sheet';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  LayoutDashboard, Building2, MapPin, Blocks, Shield, Settings,
  Flag, Activity, Plug, ChevronDown, LogOut, Menu, Store, CreditCard, Receipt, Package, Users
} from 'lucide-react';
import { POS_ADMIN_NAV } from '@/lib/posAdminNav';
import { ROUTE_MODULES, enabledModuleSet, isModuleEnabled as hasModule } from '@/lib/moduleAccess';

const BusinessContext = createContext(null);
export function useBusiness() { return useContext(BusinessContext); }

const NAV_ITEMS = [
  { label: 'Overview', icon: LayoutDashboard, path: '/' },
  { label: 'Control Center', icon: Store, path: '/control-center' },
  { type: 'separator', label: 'Management' },
  { label: 'Clients', icon: Users, path: '/clients' },
  { label: 'Businesses', icon: Building2, path: '/businesses' },
  { label: 'Outlets', icon: MapPin, path: '/outlets', moduleSlug: ROUTE_MODULES['/outlets'] },
  { label: 'Products', icon: Package, path: '/products', moduleSlug: ROUTE_MODULES['/products'] },
  { type: 'separator', label: 'POS Operations' },
  ...POS_ADMIN_NAV,
  { type: 'separator', label: 'Configuration' },
  { label: 'Modules', icon: Blocks, path: '/modules' },
  { label: 'Users & Roles', icon: Shield, path: '/users', moduleSlug: ROUTE_MODULES['/users'] },
  { type: 'separator', label: 'System' },
  { label: 'Settings', icon: Settings, path: '/settings', moduleSlug: ROUTE_MODULES['/settings'] },
  { label: 'Feature Flags', icon: Flag, path: '/feature-flags', moduleSlug: ROUTE_MODULES['/feature-flags'] },
  { type: 'separator', label: 'Billing' },
  { label: 'Plans', icon: CreditCard, path: '/plans' },
  { label: 'Subscriptions', icon: Receipt, path: '/subscriptions', moduleSlug: ROUTE_MODULES['/subscriptions'] },
  { type: 'separator', label: 'Monitoring' },
  { label: 'Audit Logs', icon: Activity, path: '/audit-logs', moduleSlug: ROUTE_MODULES['/audit-logs'] },
  { label: 'Integrations', icon: Plug, path: '/integrations', moduleSlug: ROUTE_MODULES['/integrations'] },
  { label: 'POS Bridge', icon: Plug, path: '/pos-bridge', moduleSlug: ROUTE_MODULES['/pos-bridge'] },
];

export default function DashboardLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [businesses, setBusinesses] = useState([]);
  const [selectedBusiness, setSelectedBusiness] = useState(null);
  const [businessModules, setBusinessModules] = useState([]);
  const [modulesLoading, setModulesLoading] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const moduleSet = enabledModuleSet(businessModules);

  const fetchBusinesses = useCallback(async () => {
    try {
      const { data } = await api.get('/businesses');
      setBusinesses(data);
    } catch (err) {
      console.error('Failed to fetch businesses', err);
    }
  }, []);

  useEffect(() => { fetchBusinesses(); }, [fetchBusinesses]);

  const fetchBusinessModules = useCallback(async () => {
    if (!selectedBusiness?.id) {
      setBusinessModules([]);
      setModulesLoading(false);
      return;
    }
    setModulesLoading(true);
    try {
      const { data } = await api.get(`/modules/business/${selectedBusiness.id}`);
      setBusinessModules(data || []);
    } catch {
      setBusinessModules([]);
    } finally {
      setModulesLoading(false);
    }
  }, [selectedBusiness]);

  useEffect(() => {
    fetchBusinessModules();
  }, [fetchBusinessModules]);

  const selectBusiness = (biz) => {
    setSelectedBusiness(biz);
    if (biz?.id) localStorage.setItem('selectedBusinessId', biz.id);
    else localStorage.removeItem('selectedBusinessId');
  };

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const moduleEnabled = (moduleSlug) => !selectedBusiness || modulesLoading || user?.role === 'platform_admin' || hasModule(moduleSet, moduleSlug);
  const visibleNavItems = NAV_ITEMS.filter((item, index, items) => {
    if (item.type !== 'separator') {
      return moduleEnabled(item.moduleSlug);
    }
    const nextSeparatorIndex = items.findIndex((next, nextIndex) => nextIndex > index && next.type === 'separator');
    const sectionItems = items.slice(index + 1, nextSeparatorIndex === -1 ? items.length : nextSeparatorIndex);
    return sectionItems.some(next => next.type !== 'separator' && moduleEnabled(next.moduleSlug));
  });

  const NavContent = () => (
    <ScrollArea className="flex-1 py-4">
      <nav className="space-y-0.5 px-3">
        {visibleNavItems.map((item, i) => {
          if (item.type === 'separator') {
            return (
              <div key={i} className="pt-5 pb-1.5 px-3">
                <span className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">{item.label}</span>
              </div>
            );
          }
          const Icon = item.icon;
          const active = location.pathname === item.path || (item.path !== '/' && location.pathname.startsWith(`${item.path}/`));
          return (
            <Link
              key={item.path}
              to={item.path}
              onClick={() => setMobileOpen(false)}
              data-testid={`nav-${item.label.toLowerCase().replace(/\s+&?\s*/g, '-')}`}
              className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-all duration-200 ${
                active
                  ? 'bg-white text-zinc-950 shadow-sm border border-zinc-200/60'
                  : 'text-zinc-500 hover:bg-white/60 hover:text-zinc-800'
              }`}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </ScrollArea>
  );

  return (
    <BusinessContext.Provider value={{ businesses, selectedBusiness, selectBusiness, clearBusiness: () => selectBusiness(null), refreshBusinesses: fetchBusinesses, businessModules, refreshBusinessModules: fetchBusinessModules, modulesLoading, moduleSet, isModuleEnabled: moduleEnabled }}>
      <div className="min-h-screen bg-[#F4F4F5]">
        {/* Top Navigation */}
        <header className="h-14 fixed top-0 w-full bg-white/90 backdrop-blur-lg border-b border-zinc-200 z-50 flex items-center px-4 md:px-6" data-testid="top-nav">
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="md:hidden mr-2 h-8 w-8" data-testid="mobile-menu-btn">
                <Menu className="h-4 w-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-64 p-0 bg-[#FAFAFA]">
              <div className="h-14 flex items-center gap-2 px-5 border-b border-zinc-200">
                <Store className="h-5 w-5 text-blue-600" />
                <span className="font-heading font-semibold text-zinc-900">AdminCore</span>
              </div>
              <NavContent />
            </SheetContent>
          </Sheet>

          <div className="flex items-center gap-2 mr-4">
            <Store className="h-5 w-5 text-blue-600" />
            <span className="font-heading font-semibold text-zinc-900 hidden sm:inline" data-testid="platform-logo">AdminCore</span>
          </div>

          {businesses.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" className="gap-2 h-8 text-sm border-zinc-200 shadow-sm" data-testid="business-switcher">
                  <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: selectedBusiness?.branding?.primary_color || '#0055FF' }} />
                  <span className="max-w-[160px] truncate">{selectedBusiness?.name || 'All businesses'}</span>
                  <ChevronDown className="h-3 w-3 opacity-40" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-64 border-zinc-200 shadow-lg">
                <div className="px-2 py-1.5">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">Switch Business</p>
                </div>
                <DropdownMenuItem onClick={() => selectBusiness(null)} className={!selectedBusiness ? 'bg-blue-50' : ''}>
                  <div className="flex items-center gap-2.5 flex-1">
                    <div className="w-2.5 h-2.5 rounded-full shrink-0 bg-zinc-300" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">All businesses</p>
                      <p className="text-[11px] text-zinc-400">Platform-wide admin</p>
                    </div>
                  </div>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                {businesses.map(biz => (
                  <DropdownMenuItem
                    key={biz.id}
                    onClick={() => selectBusiness(biz)}
                    data-testid={`business-option-${biz.slug}`}
                    className={selectedBusiness?.id === biz.id ? 'bg-blue-50' : ''}
                  >
                    <div className="flex items-center gap-2.5 flex-1">
                      <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: biz.branding?.primary_color || '#0055FF' }} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">{biz.name}</p>
                        <p className="text-[11px] text-zinc-400">{biz.type}</p>
                      </div>
                      <Badge variant="outline" className="text-[10px] shrink-0">{biz.plan}</Badge>
                    </div>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          <div className="flex-1" />

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" className="gap-2 h-8 text-sm" data-testid="user-menu">
                <div className="w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center">
                  <span className="text-[10px] font-semibold text-white">{user?.name?.[0]?.toUpperCase() || 'U'}</span>
                </div>
                <span className="hidden sm:inline text-zinc-700 font-medium">{user?.name}</span>
                <ChevronDown className="h-3 w-3 opacity-40" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56 border-zinc-200 shadow-lg">
              <div className="px-3 py-2">
                <p className="text-sm font-medium text-zinc-900">{user?.name}</p>
                <p className="text-xs text-zinc-500">{user?.email}</p>
                <Badge className="mt-1.5 text-[10px] bg-blue-50 text-blue-700 border-blue-100 hover:bg-blue-50">{user?.role?.replace(/_/g, ' ')}</Badge>
              </div>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleLogout} data-testid="logout-btn" className="text-red-600 focus:text-red-600">
                <LogOut className="h-4 w-4 mr-2" />
                Sign Out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>

        {/* Desktop Sidebar */}
        <aside className="fixed left-0 top-14 w-64 h-[calc(100vh-3.5rem)] bg-[#FAFAFA] border-r border-zinc-200 hidden md:flex flex-col" data-testid="sidebar">
          <NavContent />
          <div className="p-4 border-t border-zinc-200">
            <div className="text-[10px] text-zinc-400 font-medium">AdminCore v1.0</div>
          </div>
        </aside>

        {/* Main Content */}
        <main className="pt-14 md:pl-64 min-h-screen">
          <div className="max-w-7xl mx-auto p-4 md:p-8 space-y-8">
            <Outlet />
          </div>
        </main>
      </div>
    </BusinessContext.Provider>
  );
}
