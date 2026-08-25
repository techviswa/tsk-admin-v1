# AdminCore - Multi-Tenant Business Admin Panel

## Product Overview
AdminCore is a multi-tenant SaaS admin panel platform designed as a central business control hub for managing multiple businesses, modules, users, and configurations under one ecosystem.

## Architecture
- **Frontend**: React 19 + Shadcn/UI + Tailwind CSS
- **Backend**: FastAPI (Python) + MongoDB (Motor async)
- **Auth**: JWT-based with httpOnly cookies, RBAC (platform_admin, business_owner, manager, staff, support_admin)
- **Database**: MongoDB with collections for users, businesses, outlets, modules, business_modules, feature_flags, settings, audit_logs, integrations

## Core Requirements
- Multi-tenant SaaS architecture with business isolation
- Role-based and permission-based access control
- Support for multiple business types: restaurant, cafe, retail, salon, pharmacy, supermarket, custom
- Module registry with enable/disable per business
- Dynamic settings engine with categories (general, notifications, branding)
- Feature flag system per tenant
- Audit logging for all significant actions
- Integration registry scaffolding

## What's Been Implemented (April 14, 2026)
### Backend (server.py)
- JWT authentication (login, register, logout, me, refresh)
- Business CRUD with tenant isolation
- Outlet CRUD scoped to business
- Module registry + per-business module toggle
- User CRUD with RBAC enforcement
- Settings engine (list, update by key)
- Feature flags (CRUD + toggle)
- Audit log tracking (business-scoped + platform-wide)
- Integration registry (CRUD)
- Dashboard stats endpoint
- **Plan Management** (CRUD for Free/Starter/Pro/Enterprise with limits, pricing, modules, features)
- **Subscription Management** (CRUD + status transitions: active/trial/suspended/cancelled)
- **Entitlements Engine** (usage vs limits, included modules, feature checks per business)
- Database seeding with 3 demo businesses, 4 users, 5 outlets, 12 modules, 4 plans, 3 subscriptions, 6 feature flags, default settings, 4 integrations

### Frontend
- Login/Register page with tabs
- Dashboard layout (TopNav + Sidebar + Content area)
- Business Switcher dropdown in top nav
- Dashboard overview with stat cards + recent activity
- Businesses management table with CRUD
- Outlets management table with CRUD
- Modules grid with enable/disable toggles
- Users & Roles table with CRUD
- Settings page with tabs (General, Notifications, Branding)
- Feature Flags table with toggles
- Audit Logs viewer with pagination and entity filter
- Integrations card grid with CRUD
- **Plans page** - Plan tier cards with pricing, limits, features, modules, CRUD
- **Subscriptions page** - Subscription table with status management, plan changes, usage/entitlements panel, unsubscribed business detection
- Design: Light theme, Swiss/high-contrast, Outfit+Inter fonts

## User Personas
1. **Platform Admin**: Full access to all businesses, modules, settings. Can manage tenants.
2. **Business Owner**: Manages their own businesses, outlets, modules, and team.
3. **Manager**: Operational management of assigned business.
4. **Staff**: Limited access within their assigned business.
5. **Support Admin**: Cross-business view for customer support.

## Demo Accounts
- Platform Admin: admin@admin.com / admin123
- Business Owner: john@sunrise.com / password123
- Manager: sarah@urban.com / password123
- Staff: mike@sunrise.com / password123

## P0 Backlog (High Priority)
- Permission granularity per module per role
- Plan/subscription management with limits
- Invite flow (email-based user invitations)
- Proper password reset flow

## P1 Backlog (Medium Priority)
- Stripe subscription billing integration
- Email notifications (SendGrid)
- Webhook system with retry handling
- Advanced analytics dashboard with charts
- White-label branding (custom domain, logo, colors)
- Module plugin architecture (dynamic route registration)

## P2 Backlog (Lower Priority)
- API key management for external app connections
- Real-time notifications (WebSocket)
- Multi-language support (i18n)
- Export/import data functionality
- Activity digest emails
- Mobile-responsive sidebar drawer improvements
- Dark mode theme support

## Next Tasks
1. Implement granular permissions matrix (per-role, per-module)
2. Add Stripe subscription/billing integration
3. Build API key management for external apps to connect
4. Add analytics charts (Recharts) to dashboard
5. Implement email notification system
