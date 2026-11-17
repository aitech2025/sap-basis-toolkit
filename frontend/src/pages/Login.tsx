import { useEffect, useState } from "react";
import { api, setApiKey, setToken } from "../api";
import { Button } from "../components/ui/button";

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
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <div className="relative hidden overflow-hidden bg-sidebar p-10 text-sidebar-foreground lg:flex lg:flex-col">
        <div className="flex items-center gap-2.5">
          <svg viewBox="0 0 32 32" className="h-9 w-9" aria-hidden="true">
            <rect width="32" height="32" rx="8" fill="hsl(var(--primary))" />
            <path d="M8 18.5h4l2.5-6 3 10 2.5-6h4" fill="none" stroke="white" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <div className="leading-tight"><div className="text-base font-semibold">Sentinel</div><div className="text-xs text-sidebar-muted">SAP Basis operations</div></div>
        </div>
        <div className="mt-auto max-w-md">
          <p className="text-[28px] font-semibold leading-tight tracking-tight">Every SAP system, from development to production, in one console.</p>
          <p className="mt-4 text-sm text-sidebar-muted">Availability, work processes, dumps, interfaces, HANA health, certificates and controlled start/stop for ECC, S/4HANA and NetWeaver.</p>
        </div>
        <svg className="pointer-events-none mt-24 w-full max-w-[400px] opacity-25" viewBox="0 0 400 180" aria-hidden="true">
          {["DEV", "QAS", "PRD"].map((env, i) => (
            <g key={env} transform={`translate(${i * 140}, ${i * 34})`}>
              <rect width="112" height="64" rx="10" fill="none" stroke="currentColor" strokeWidth="1.5" />
              <text x="16" y="38" fill="currentColor" fontFamily="IBM Plex Mono" fontSize="20" fontWeight="600">{env}</text>
              {i < 2 && <path d={`M112 32 C 128 32, 128 ${66}, 140 ${66}`} fill="none" stroke="currentColor" strokeWidth="1.5" />}
            </g>
          ))}
        </svg>
      </div>
      <div className="flex items-center justify-center px-5 py-12">
        <form className="w-full max-w-sm space-y-5" onSubmit={submit}>
          <div className="space-y-1.5">
            <h1 className="text-2xl">{mode === "setup" ? "Create the first administrator" : mode === "apikey" ? "Sign in with the API key" : "Sign in to Sentinel"}</h1>
            <p className="text-sm text-muted-foreground">
              {mode === "setup" ? "No users exist yet. Enter the server API key (API_AUTH_TOKEN) to prove you operate this server, then choose the admin credentials."
                : mode === "apikey" ? "For automation and break-glass access. The key grants admin rights." : "Use the account your Sentinel administrator created for you."}
            </p>
          </div>
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
          <Button type="submit" className="w-full" disabled={busy}>{busy ? "Signing in…" : mode === "setup" ? "Create admin and sign in" : "Sign in"}</Button>
          <div className="text-center">
            {mode === "login" && <button type="button" className="link-btn text-xs" onClick={() => setMode("apikey")}>Use the server API key instead</button>}
            {mode === "apikey" && <button type="button" className="link-btn text-xs" onClick={() => setMode("login")}>Back to user sign-in</button>}
          </div>
        </form>
      </div>
    </div>
  );
}
