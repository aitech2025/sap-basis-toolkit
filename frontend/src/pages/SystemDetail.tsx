import { useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { SystemForm } from "../components/SystemForm";
import { RequireRole } from "../components/session";
import { ErrorBanner, Pill, useAsync } from "../components/ui";
import AvailabilityTab from "./system/AvailabilityTab";
import CertificatesTab from "./system/CertificatesTab";
import ErrorsTab from "./system/ErrorsTab";
import HanaTab from "./system/HanaTab";
import InterfacesTab from "./system/InterfacesTab";
import GrowthTab from "./system/GrowthTab";
import HistoryTab from "./system/HistoryTab";
import InstancesTab from "./system/InstancesTab";
import JobsTab from "./system/JobsTab";
import LocksTab from "./system/LocksTab";
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
  const abap = system.stack !== "JAVA";
  const tabs = [
    ["", "Overview"], ["instances", "Instances & control"], ["availability", "Availability"],
    ...(abap ? [["errors", "Errors"], ["interfaces", "Interfaces"]] : []),
    ...(system.database === "HANA" ? [["hana", "HANA"]] : []),
    ["certificates", "Certificates"], ["growth", "Table growth"],
    ["users", "Users & licences"],
    ["transports", system.capabilities.transports.supported ? "Transports" : "Transports (n/a)"],
    ["jobs", system.capabilities.jobs.supported ? "Background jobs" : "Background jobs (n/a)"],
    ["locks", system.capabilities.locks.supported ? "SAP locks" : "SAP locks (n/a)"],
    ["history", "History"], ["setup", "Setup & export guide"],
  ] as [string, string][];

  return (
    <>
      <section className="system-masthead">
        <div className="system-masthead-main">
          <div className="system-breadcrumb"><NavLink to="/systems">SAP systems</NavLink><span>/</span><NavLink to={`/landscapes/${encodeURIComponent(system.landscape)}`}>{system.landscape}</NavLink><span>/</span><strong>{system.sid}</strong></div>
          <div className="system-title-row">
            <div className="system-monogram">{system.sid.slice(0, 1)}</div>
            <div><span className="eyebrow">SAP SYSTEM WORKSPACE · {system.environment}</span><h1>{system.sid} <span>{system.name}</span></h1></div>
            <Pill value={system.enabled ? "enabled" : "disabled"} />
          </div>
          <div className="system-meta-strip">
            <span>{system.product_label}{system.product_version ? ` ${system.product_version}` : ""}</span>
            <i />
            <span>{system.stack}</span>
            <i />
            <span>{system.database}</span>
            {system.host && <><i /><span>{system.host}</span></>}
            {system.basis_release && <><i /><span>SAP_BASIS {system.basis_release}</span></>}
          </div>
        </div>
        <div className="system-masthead-actions">
          <RequireRole role="operator">
            <button className="btn" onClick={() => setEditing(true)}>Edit system</button>
            <button className="btn danger" onClick={remove}>Delete</button>
          </RequireRole>
        </div>
      </section>
      <nav className="system-tabs">
        {tabs.map(([path, label]) => (
          <NavLink key={path} to={`/systems/${id}${path ? `/${path}` : ""}`} end={!path}>{label}</NavLink>
        ))}
      </nav>
      {/* key: remount every tab per system so uploads/results never carry over between systems */}
      <Routes key={system.id}>
        <Route index element={<OverviewTab system={system} />} />
        <Route path="setup" element={<SetupTab system={system} />} />
        <Route path="instances" element={<InstancesTab system={system} />} />
        <Route path="availability" element={<AvailabilityTab system={system} />} />
        <Route path="errors" element={<ErrorsTab system={system} />} />
        <Route path="interfaces" element={<InterfacesTab system={system} />} />
        <Route path="hana" element={<HanaTab system={system} />} />
        <Route path="certificates" element={<CertificatesTab system={system} />} />
        <Route path="growth" element={<GrowthTab system={system} />} />
        <Route path="users" element={<UsersTab system={system} />} />
        <Route path="transports" element={<TransportsTab system={system} />} />
        <Route path="jobs" element={<JobsTab system={system} />} />
        <Route path="locks" element={<LocksTab system={system} />} />
        <Route path="history" element={<HistoryTab system={system} />} />
        <Route path="*" element={<Navigate to={`/systems/${id}`} replace />} />
      </Routes>
      <SystemForm open={editing} initial={system} catalogue={catalogue.data} landscapes={[system.landscape]}
                  onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />
    </>
  );
}
