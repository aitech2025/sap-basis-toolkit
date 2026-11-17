import { useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { SystemForm } from "../components/SystemForm";
import { ErrorBanner, Pill, useAsync } from "../components/ui";
import CertificatesTab from "./system/CertificatesTab";
import GrowthTab from "./system/GrowthTab";
import HistoryTab from "./system/HistoryTab";
import OverviewTab from "./system/OverviewTab";
import SetupTab from "./system/SetupTab";
import TransportsTab from "./system/TransportsTab";
import UsersTab from "./system/UsersTab";

export default function SystemDetailPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const { data: system, error, reload } = useAsync(() => api.system(id), [id]);
  const catalogue = useAsync(() => api.catalogue(), []);
  const [editing, setEditing] = useState(false);

  const remove = async () => {
    if (!system) return;
    if (!window.confirm(`Delete ${system.sid} (${system.landscape}) and all its monitoring history? Endpoints are kept but unassigned.`)) return;
    await api.deleteSystem(system.id);
    navigate("/systems");
  };

  if (error) return <ErrorBanner error={error} />;
  if (!system || system.id !== id) return <p className="muted">Loading…</p>;
  const tabs = [
    ["", "Overview"], ["certificates", "Certificates"], ["growth", "Table growth"],
    ["users", "Users & licences"],
    ["transports", system.capabilities.transports.supported ? "Transports" : "Transports (n/a)"],
    ["history", "History"], ["setup", "Setup & export guide"],
  ] as const;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{system.sid} <span className="muted" style={{ fontWeight: 500 }}>· {system.name}</span></h1>
          <p>
            <span className="tag">{system.environment}</span>{system.product_label}
            {system.product_version && ` ${system.product_version}`} · {system.stack} · {system.database}
            {" "}· landscape {system.landscape} {!system.enabled && <Pill value="disabled" />}
          </p>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => setEditing(true)}>Edit</button>
          <button className="btn danger" onClick={remove}>Delete</button>
        </div>
      </div>
      <nav className="tabs">
        {tabs.map(([path, label]) => (
          <NavLink key={path} to={`/systems/${id}${path ? `/${path}` : ""}`} end={!path}>{label}</NavLink>
        ))}
      </nav>
      {/* key: remount every tab per system so uploads/results never carry over between systems */}
      <Routes key={system.id}>
        <Route index element={<OverviewTab system={system} />} />
        <Route path="setup" element={<SetupTab system={system} />} />
        <Route path="certificates" element={<CertificatesTab system={system} />} />
        <Route path="growth" element={<GrowthTab system={system} />} />
        <Route path="users" element={<UsersTab system={system} />} />
        <Route path="transports" element={<TransportsTab system={system} />} />
        <Route path="history" element={<HistoryTab system={system} />} />
        <Route path="*" element={<Navigate to={`/systems/${id}`} replace />} />
      </Routes>
      <SystemForm open={editing} initial={system} catalogue={catalogue.data} landscapes={[system.landscape]}
                  onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />
    </>
  );
}
