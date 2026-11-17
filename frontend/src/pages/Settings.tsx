import { useState } from "react";
import { api, getApiKey, setApiKey } from "../api";
import { Card } from "../components/ui";

export default function Settings() {
  const [key, setKey] = useState(getApiKey());
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);

  const save = async () => {
    setApiKey(key.trim());
    try {
      await api.systems();
      setStatus({ ok: true, text: "Connected. The key is kept for this browser session only." });
    } catch (e) {
      setStatus({ ok: false, text: (e as Error).message });
    }
  };

  return (
    <>
      <div className="page-head"><div><h1>Settings</h1><p>Connection to the Sentinel API.</p></div></div>
      <Card title="API key">
        <div className="row">
          <label className="field" style={{ flex: 1, maxWidth: 420 }}>
            <span>X-API-Key</span>
            <input type="password" value={key} autoComplete="off" onChange={(e) => setKey(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && save()} />
          </label>
          <button className="btn primary" onClick={save}>Save &amp; test</button>
          <button className="btn" onClick={() => { setKey(""); setApiKey(""); setStatus(null); }}>Clear</button>
        </div>
        {status && <div className={`banner ${status.ok ? "ok" : "error"}`} style={{ marginTop: 12 }}>{status.text}</div>}
        <p className="muted">
          The key is the server's <code>API_AUTH_TOKEN</code>. Alert channels (Slack, Teams, PagerDuty, email),
          thresholds and allowlists are configured on the server through environment variables.
        </p>
      </Card>
    </>
  );
}
