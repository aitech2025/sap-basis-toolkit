import { Suspense, lazy, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { Skeleton } from "./components/ui/skeleton";
import { hasCredentials } from "./api";
import { RequireRole, SessionProvider } from "./components/session";
import Login from "./pages/Login";

const Account = lazy(() => import("./pages/Account"));
const Analytics = lazy(() => import("./pages/Analytics"));
const Alerts = lazy(() => import("./pages/Alerts"));
const Audit = lazy(() => import("./pages/Audit"));
const Certificates = lazy(() => import("./pages/Certificates"));
const DailyChecks = lazy(() => import("./pages/DailyChecks"));
const Discover = lazy(() => import("./pages/Discover"));
const IncidentDetail = lazy(() => import("./pages/IncidentDetail"));
const Incidents = lazy(() => import("./pages/Incidents"));
const Intelligence = lazy(() => import("./pages/Intelligence"));
const LandscapeDetail = lazy(() => import("./pages/LandscapeDetail"));
const Landscapes = lazy(() => import("./pages/Landscapes"));
const Maintenance = lazy(() => import("./pages/Maintenance"));
const OverviewPage = lazy(() => import("./pages/Overview"));
const Rules = lazy(() => import("./pages/Rules"));
const SystemDetailPage = lazy(() => import("./pages/SystemDetail"));
const Systems = lazy(() => import("./pages/Systems"));
const Users = lazy(() => import("./pages/Users"));

function Shell() {
  return (
    <AppShell>
      <Suspense fallback={<div className="space-y-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-64 w-full" /></div>}>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/intelligence" element={<Intelligence />} />
            <Route path="/incidents" element={<Incidents />} />
            <Route path="/daily-checks" element={<DailyChecks />} />
            <Route path="/incidents/:id" element={<IncidentDetail />} />
            <Route path="/landscapes" element={<Landscapes />} />
            <Route path="/landscapes/:name" element={<LandscapeDetail />} />
            <Route path="/systems" element={<Systems />} />
            <Route path="/discover" element={<RequireRole role="admin" fallback={<p className="muted">System discovery needs the admin role.</p>}><Discover /></RequireRole>} />
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
      </Suspense>
    </AppShell>
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
