import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, hasCredentials } from "./api";
import { RequireRole, SessionProvider, useSession } from "./components/session";
import Account from "./pages/Account";
import Analytics from "./pages/Analytics";
import Alerts from "./pages/Alerts";
import Audit from "./pages/Audit";
import Certificates from "./pages/Certificates";
import Discover from "./pages/Discover";
import IncidentDetail from "./pages/IncidentDetail";
import Incidents from "./pages/Incidents";
import LandscapeDetail from "./pages/LandscapeDetail";
import Landscapes from "./pages/Landscapes";
import Login from "./pages/Login";
import Maintenance from "./pages/Maintenance";
import OverviewPage from "./pages/Overview";
import Rules from "./pages/Rules";
import SystemDetailPage from "./pages/SystemDetail";
import Systems from "./pages/Systems";
import Users from "./pages/Users";

function IncidentBadge() {
  const [count, setCount] = useState<{ total: number; critical: number } | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.incidentCounts().then((c) => alive && setCount(c)).catch(() => {});
    load();
    const timer = window.setInterval(load, 60000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);
  if (!count?.total) return null;
  return <span className={`nav-count ${count.critical ? "is-critical" : ""}`}>{count.total}</span>;
}

const pageNames: Record<string, string> = {
  "/": "Overview", "/analytics": "Monitoring analytics", "/incidents": "Incidents", "/landscapes": "Landscapes",
  "/systems": "SAP systems", "/discover": "System discovery", "/certificates": "Certificates",
  "/maintenance": "Maintenance", "/rules": "Alert rules", "/alerts": "Notifications",
  "/users": "Users & access", "/audit": "Audit log", "/account": "Account",
};

function NavItem({ to, icon, children, end = false }: { to: string; icon: string; children: React.ReactNode; end?: boolean }) {
  return <NavLink to={to} end={end} className="nav-item"><span className="nav-icon" aria-hidden="true">{icon}</span><span className="nav-label">{children}</span>{to === "/incidents" && <IncidentBadge />}</NavLink>;
}

function Shell() {
  const { me, logout } = useSession();
  const location = useLocation();
  const pageTitle = pageNames[location.pathname] || (location.pathname.startsWith("/systems/") ? "System workspace" : location.pathname.startsWith("/incidents/") ? "Incident details" : "Monitoring");
  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand-lockup">
          <div className="brand-mark">S<span>•</span></div>
          <div className="brand-copy"><strong>SENTINEL</strong><small>SAP OPERATIONS CENTER</small></div>
        </div>
        <div className="workspace-switcher"><span className="workspace-dot" /><span><small>WORKSPACE</small><strong>Primary landscape</strong></span><span className="switch-chevron">⌄</span></div>
        <div className="nav-section">MONITOR</div>
        <nav className="nav-group">
          <NavItem to="/" icon="◫" end>Overview</NavItem>
          <NavItem to="/analytics" icon="⌁">Analytics</NavItem>
          <NavItem to="/incidents" icon="⚑">Incidents</NavItem>
          <NavItem to="/landscapes" icon="⌘">Landscapes</NavItem>
          <NavItem to="/systems" icon="▦">SAP systems</NavItem>
          <NavItem to="/certificates" icon="◇">Certificates</NavItem>
        </nav>
        <div className="nav-section">OPERATE</div>
        <nav className="nav-group">
          <NavItem to="/maintenance" icon="◷">Maintenance windows</NavItem>
          <NavItem to="/rules" icon="⚙">Alert rules</NavItem>
          <NavItem to="/alerts" icon="↗">Notifications</NavItem>
          <RequireRole role="admin"><NavItem to="/discover" icon="⌕">System discovery</NavItem></RequireRole>
        </nav>
        <RequireRole role="admin">
          <div className="nav-section">ADMINISTRATION</div>
          <nav className="nav-group">
            <NavItem to="/users" icon="♙">Users & access</NavItem>
            <NavItem to="/audit" icon="≡">Audit log</NavItem>
          </nav>
        </RequireRole>
        <div className="sidebar-spacer" />
        <div className="sidebar-status"><span className="live-dot" /><span><strong>Monitoring active</strong><small>Collector connectivity varies by system</small></span></div>
        <NavLink to="/account" className="profile-link">
          <span className="avatar">{(me?.display_name || me?.username || "U").slice(0,1).toUpperCase()}</span>
          <span className="profile-copy"><strong>{me ? (me.display_name || me.username) : "Loading…"}</strong><small>{me?.role || "User"}{me?.kind === "apikey" ? " · API key" : ""}</small></span>
          <span className="profile-more">•••</span>
        </NavLink>
        <button className="signout-link" onClick={logout}>↪ <span>Sign out</span></button>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <div className="breadcrumb"><span>Sentinel</span><span className="crumb-sep">/</span><strong>{pageTitle}</strong></div>
          <div className="topbar-actions">
            <span className="environment-chip"><span className="live-dot" /> LIVE MONITORING</span>
            <span className="topbar-divider" />
            <NavLink to="/account" className="topbar-icon" aria-label="Account">⚙</NavLink>
            <span className="topbar-date">{new Date().toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short" })}</span>
          </div>
        </header>
        <main className="main-content">
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/incidents" element={<Incidents />} />
            <Route path="/incidents/:id" element={<IncidentDetail />} />
            <Route path="/landscapes" element={<Landscapes />} />
            <Route path="/landscapes/:name" element={<LandscapeDetail />} />
            <Route path="/systems" element={<Systems />} />
            <Route path="/discover" element={<RequireRole role="admin" fallback={<p className="muted">Discovery needs the admin role.</p>}><Discover /></RequireRole>} />
            <Route path="/systems/:id/*" element={<SystemDetailPage />} />
            <Route path="/certificates" element={<Certificates />} />
            <Route path="/maintenance" element={<Maintenance />} />
            <Route path="/rules" element={<Rules />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/users" element={<Users />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/account" element={<Account />} />
            <Route path="/settings" element={<Navigate to="/account" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const [signedIn, setSignedIn] = useState(hasCredentials());
  useEffect(() => {
    const onAuth = () => setSignedIn(false);
    window.addEventListener("auth-required", onAuth);
    return () => window.removeEventListener("auth-required", onAuth);
  }, []);
  if (!signedIn) return <Login onSignedIn={() => setSignedIn(true)} />;
  return <SessionProvider onSignedOut={() => setSignedIn(false)}><Shell /></SessionProvider>;
}
