import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, hasCredentials } from "./api";
import { RequireRole, SessionProvider, useSession } from "./components/session";
import Account from "./pages/Account";
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
  return <span className={`badge ${count.critical ? "bad" : "warn"}`} aria-label={`${count.total} active incidents`}>{count.total}</span>;
}

function Shell() {
  const { me, logout } = useSession();
  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <div className="brand">SAP Basis Toolkit<small>Sentinel · SAP monitoring</small></div>
        <NavLink to="/" end>Landscape overview</NavLink>
        <NavLink to="/incidents">Incidents <IncidentBadge /></NavLink>
        <NavLink to="/landscapes">Landscapes</NavLink>
        <NavLink to="/systems">Systems</NavLink>
        <RequireRole role="admin"><NavLink to="/discover">Discover system</NavLink></RequireRole>
        <NavLink to="/certificates">Certificates</NavLink>
        <div className="nav-section">Operations</div>
        <NavLink to="/maintenance">Maintenance windows</NavLink>
        <NavLink to="/rules">Alert rules</NavLink>
        <NavLink to="/alerts">Notifications</NavLink>
        <RequireRole role="admin">
          <div className="nav-section">Administration</div>
          <NavLink to="/users">Users</NavLink>
          <NavLink to="/audit">Audit log</NavLink>
        </RequireRole>
        <div className="spacer" />
        <NavLink to="/account" className="who">
          {me ? <><strong>{me.display_name || me.username}</strong><small>{me.role}{me.kind === "apikey" ? " · API key" : ""}</small></> : "…"}
        </NavLink>
        <button className="link-btn" onClick={logout}>Sign out</button>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
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
  return (
    <SessionProvider onSignedOut={() => setSignedIn(false)}>
      <Shell />
    </SessionProvider>
  );
}
