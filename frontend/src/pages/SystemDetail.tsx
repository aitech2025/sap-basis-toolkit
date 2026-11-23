import { useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { LayerBadges } from "../components/MonitoringLayers";
import { SystemForm } from "../components/SystemForm";
import type { MonitoringLayer } from "../types";
import { RequireRole } from "../components/session";
import { Pencil, Trash2 } from "lucide-react";
import { ErrorBanner, Pill, useAsync } from "../components/ui";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import { cn } from "../lib/utils";
import AvailabilityTab from "./system/AvailabilityTab";
import CertificatesTab from "./system/CertificatesTab";
import ErrorsTab from "./system/ErrorsTab";
import BasisChecksTab from "./system/BasisChecksTab";
import HanaTab from "./system/HanaTab";
import InterfacesTab from "./system/InterfacesTab";
import GrowthTab from "./system/GrowthTab";
import HistoryTab from "./system/HistoryTab";
import InfrastructureTab from "./system/InfrastructureTab";
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
  if (!system || system.id !== id) return <div className="space-y-4"><Skeleton className="h-14 w-96" /><Skeleton className="h-9 w-full" /><Skeleton className="h-64 w-full" /></div>;
  const abap = system.stack !== "JAVA";
  const off = (label: string, layer: MonitoringLayer) => (system.monitoring_layers.includes(layer) ? label : `${label} (off)`);
  const tabs = [
    ["", "Overview"], ["infrastructure", "Infrastructure"], ["instances", "Instances & control"],
    ["availability", off("Availability", "application")],
    ...(abap ? [["basis", "Basis checks"], ["errors", "Errors"], ["interfaces", "Interfaces"]] : []),
    ...(system.database === "HANA" ? [["hana", off("HANA", "database")]] : []),
    ["certificates", "Certificates"], ["growth", "Table growth"],
    ["users", "Users & licences"],
    ["transports", system.capabilities.transports.supported ? "Transports" : "Transports (n/a)"],
    ["jobs", system.capabilities.jobs.supported ? "Background jobs" : "Background jobs (n/a)"],
    ["locks", system.capabilities.locks.supported ? "SAP locks" : "SAP locks (n/a)"],
    ["history", "History"], ["setup", "Setup & export guide"],
  ] as [string, string][];

  const host = system.host ? <span className="font-mono">{system.host}</span> : null;
  return (
    <>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-4">
          <div className="flex h-16 min-w-[80px] items-center justify-center rounded-3xl bg-gradient-to-br from-[hsl(252_90%_72%)] to-primary px-4 font-mono text-2xl font-bold tracking-tight text-white shadow-[0_14px_30px_-14px_hsl(var(--primary))]">
            {system.sid}
          </div>
          <div className="min-w-0 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate">{system.name}</h1>
              <Badge variant={system.environment === "PRD" ? "bad" : "secondary"} className="font-mono">{system.environment}</Badge>
              {!system.enabled && <Pill value="disabled" />}
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
              <span>{system.product_label}{system.product_version ? ` ${system.product_version}` : ""}</span>
              <span>{system.stack} on {system.database}</span>
              {system.basis_release && <span>SAP_BASIS {system.basis_release}</span>}
              {host}
              <NavLink to={`/landscapes/${encodeURIComponent(system.landscape)}`}>Landscape {system.landscape}</NavLink>
              <span className="inline-flex items-center gap-1.5">Monitoring <LayerBadges layers={system.monitoring_layers} /></span>
            </div>
          </div>
        </div>
        <RequireRole role="operator">
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setEditing(true)}><Pencil />Edit</Button>
            <Button variant="outline" className="text-bad hover:bg-bad-soft hover:text-bad" onClick={remove}><Trash2 />Delete</Button>
          </div>
        </RequireRole>
      </div>
      <nav aria-label="System sections" className="-mx-4 mb-6 overflow-x-auto px-4 lg:-mx-8 lg:px-8">
        <div className="flex w-max gap-1 rounded-full bg-card p-1.5">
          {tabs.map(([path, label]) => (
            <NavLink key={path} to={`/systems/${id}${path ? `/${path}` : ""}`} end={!path}
              className={({ isActive }) => cn("whitespace-nowrap rounded-full px-4 py-2 text-sm font-semibold text-muted-foreground transition-colors hover:text-foreground hover:no-underline",
                isActive && "bg-foreground text-background hover:text-background")}>
              {label}
            </NavLink>
          ))}
        </div>
      </nav>
      {/* key: remount every tab per system so uploads/results never carry over between systems */}
      <Routes key={system.id}>
        <Route index element={<OverviewTab system={system} />} />
        <Route path="setup" element={<SetupTab system={system} />} />
        <Route path="infrastructure" element={<InfrastructureTab system={system} />} />
        <Route path="instances" element={<InstancesTab system={system} />} />
        <Route path="availability" element={<AvailabilityTab system={system} />} />
        <Route path="basis" element={<BasisChecksTab system={system} />} />
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
