import { AbapAgentCard } from "../../components/AbapAgentCard";
import { CollectorsCard } from "../../components/Collectors";
import DependencyTopologyCard from "./DependencyTopologyCard";
import { Card, Pill } from "../../components/ui";
import type { SystemDetail } from "../../types";

const MODULES = [
  ["certificates", "Certificates"], ["growth", "Table growth"], ["users", "Users & licenses"], ["transports", "Transports"],
  ["jobs", "Background jobs"], ["locks", "SAP locks"],
] as const;

export default function SetupTab({ system }: { system: SystemDetail }) {
  const caps = system.capabilities;
  const lm = caps.license_model;
  return (
    <>
      <CollectorsCard system={system} />
      <AbapAgentCard system={system} />
      <DependencyTopologyCard system={system} />
      <div className="cols">
        <Card title="System">
          <table>
            <tbody>
              <tr><th>Product</th><td>{system.product_label} {system.product_version}</td></tr>
              <tr><th>SAP_BASIS / NW release</th><td>{system.basis_release ?? "–"}</td></tr>
              <tr><th>Stack</th><td>{system.stack}</td></tr>
              <tr><th>Database</th><td>{system.database}</td></tr>
              <tr><th>Host</th><td>{system.host ?? "–"}</td></tr>
              <tr><th>Default client</th><td>{system.default_client ?? "–"}</td></tr>
              <tr><th>Timezone</th><td>{system.timezone ?? "UTC (default)"}</td></tr>
              <tr><th>Description</th><td>{system.description ?? "–"}</td></tr>
            </tbody>
          </table>
        </Card>
        <Card title="Licensing">
          <p><strong>{lm.label}</strong>{system.license_model ? " (overridden)" : " (product default)"}</p>
          <p className="muted">Tiers: {Object.values(lm.tiers).join(" → ")}</p>
          <p className="muted">{lm.note}</p>
        </Card>
      </div>
      <Card title="Module support and export guide">
        <p className="muted">How to get the data each module needs from this {system.product_label} system on {system.database}.</p>
        {MODULES.map(([key, label]) => (
          <div key={key} style={{ marginBottom: 14 }}>
            <h3 style={{ marginBottom: 6 }}>{label} <Pill value={caps[key].supported ? "enabled" : "disabled"} /></h3>
            <pre>{caps[key].how_to_export}</pre>
          </div>
        ))}
      </Card>
    </>
  );
}
