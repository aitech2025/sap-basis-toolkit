import { useEffect, useState } from "react";
import { api, setApiKey, setToken } from "../api";

export default function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const [mode, setMode] = useState<"login" | "setup" | "apikey">("login");
  const [form, setForm] = useState({ username: "", password: "", confirm: "", api_key: "", display_name: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.authStatus().then((s) => s.setup_required && setMode("setup")).catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "setup") {
        if (form.password !== form.confirm) throw new Error("Passwords do not match");
        await api.setup({ api_key: form.api_key, username: form.username, password: form.password, display_name: form.display_name });
      }
      if (mode === "apikey") {
        setApiKey(form.api_key);
        await api.me();
      } else {
        const r = await api.login(form.username, form.password);
        setApiKey("");
        setToken(r.token);
      }
      onSignedIn();
    } catch (err) {
      if (mode === "apikey") setApiKey("");
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="brand-lg">SAP Basis Toolkit<small>Sentinel · end-to-end SAP monitoring</small></div>
        <h2>{mode === "setup" ? "Create the first administrator" : mode === "apikey" ? "Sign in with API key" : "Sign in"}</h2>
        {mode === "setup" && <p className="muted small">No users exist yet. Prove you operate this server with its API key (API_AUTH_TOKEN), then choose the admin's credentials.</p>}
        {error && <div className="banner error" role="alert">{error}</div>}
        {(mode === "setup" || mode === "apikey") && (
          <label className="field"><span>Server API key</span><input type="password" autoComplete="off" required value={form.api_key} onChange={set("api_key")} /></label>
        )}
        {mode !== "apikey" && (
          <>
            <label className="field"><span>Username</span><input autoComplete="username" required value={form.username} onChange={set("username")} autoFocus /></label>
            {mode === "setup" && <label className="field"><span>Display name</span><input value={form.display_name} onChange={set("display_name")} /></label>}
            <label className="field"><span>Password</span><input type="password" autoComplete={mode === "setup" ? "new-password" : "current-password"} required value={form.password} onChange={set("password")} />
              {mode === "setup" && <small>At least 12 characters, not containing the username.</small>}</label>
            {mode === "setup" && <label className="field"><span>Confirm password</span><input type="password" autoComplete="new-password" required value={form.confirm} onChange={set("confirm")} /></label>}
          </>
        )}
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Please wait…" : mode === "setup" ? "Create admin & sign in" : "Sign in"}</button>
        {mode === "login" && <button type="button" className="link-btn" onClick={() => setMode("apikey")}>Use the server API key instead (automation / break-glass)</button>}
        {mode === "apikey" && <button type="button" className="link-btn" onClick={() => setMode("login")}>Back to user sign-in</button>}
      </form>
    </div>
  );
}
