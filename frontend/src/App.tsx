import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { getApiKey } from "./api";
import Alerts from "./pages/Alerts";
import Certificates from "./pages/Certificates";
import OverviewPage from "./pages/Overview";
import Settings from "./pages/Settings";
import SystemDetailPage from "./pages/SystemDetail";
import Systems from "./pages/Systems";

export default function App() {
  const [hasKey, setHasKey] = useState(!!getApiKey());
  useEffect(() => {
    const onChange = () => setHasKey(!!getApiKey());
    window.addEventListener("apikey-changed", onChange);
    return () => window.removeEventListener("apikey-changed", onChange);
  }, []);

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <div className="brand">SAP Basis Toolkit<small>Sentinel · multi-system</small></div>
        <NavLink to="/" end>Landscape overview</NavLink>
        <NavLink to="/systems">Systems</NavLink>
        <NavLink to="/certificates">Certificates</NavLink>
        <NavLink to="/alerts">Alerts</NavLink>
        <NavLink to="/settings">Settings</NavLink>
        <div className="spacer" />
        <div className="key-state">{hasKey ? "API key set for this session" : "No API key set"}</div>
      </nav>
      <main>
        {!hasKey && (
          <div className="banner info">
            Enter your API key in <NavLink to="/settings">Settings</NavLink> to load data.
          </div>
        )}
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/systems" element={<Systems />} />
          <Route path="/systems/:id/*" element={<SystemDetailPage />} />
          <Route path="/certificates" element={<Certificates />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
