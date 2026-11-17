import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, hasCredentials, signOut } from "../api";
import type { Me, Role } from "../types";

const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

interface Session {
  me: Me | null;
  can: (role: Role) => boolean;
  refresh: () => void;
  logout: () => void;
}

const SessionContext = createContext<Session>({ me: null, can: () => false, refresh: () => {}, logout: () => {} });

export function SessionProvider({ children, onSignedOut }: { children: ReactNode; onSignedOut: () => void }) {
  const [me, setMe] = useState<Me | null>(null);
  const refresh = useCallback(() => {
    if (!hasCredentials()) {
      setMe(null);
      return;
    }
    api.me().then(setMe).catch(() => setMe(null));
  }, []);
  useEffect(() => {
    refresh();
    window.addEventListener("apikey-changed", refresh);
    return () => window.removeEventListener("apikey-changed", refresh);
  }, [refresh]);
  const logout = () => {
    signOut();
    setMe(null);
    onSignedOut();
  };
  const can = (role: Role) => !!me && RANK[me.role] >= RANK[role];
  return <SessionContext.Provider value={{ me, can, refresh, logout }}>{children}</SessionContext.Provider>;
}

export const useSession = () => useContext(SessionContext);

/** Renders children only when the signed-in role is sufficient. */
export function RequireRole({ role, children, fallback = null }: { role: Role; children: ReactNode; fallback?: ReactNode }) {
  const { can } = useSession();
  return <>{can(role) ? children : fallback}</>;
}
