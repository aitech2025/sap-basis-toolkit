import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  BarChart3, Bell, BellRing, BrainCircuit, CalendarClock, ChevronRight, FileClock, KeyRound, PanelLeft, UserRound,
  LayoutDashboard, LogOut, Menu, Monitor, Moon, Network, Radar, Search, Server, ShieldCheck, SlidersHorizontal, Sun,
  TriangleAlert, UserCog, Users,
} from "lucide-react";
import { api } from "@/api";
import { cn } from "@/lib/utils";
import { useTheme, type Theme } from "@/lib/theme";
import type { IncidentCounts, Role, SapSystem } from "@/types";
import { useSession } from "./session";
import { Avatar, AvatarFallback } from "./ui/avatar";
import { Button } from "./ui/button";
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "./ui/command";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle } from "./ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

interface NavEntry { to: string; label: string; icon: typeof Server; end?: boolean; role?: Role; badge?: boolean }
const NAV: { title: string; items: NavEntry[] }[] = [
  {
    title: "Monitor",
    items: [
      { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
      { to: "/incidents", label: "Incidents", icon: TriangleAlert, badge: true },
      { to: "/landscapes", label: "Landscapes", icon: Network },
      { to: "/systems", label: "SAP systems", icon: Server },
      { to: "/analytics", label: "Analytics", icon: BarChart3 },
      { to: "/intelligence", label: "Intelligence", icon: BrainCircuit },
      { to: "/certificates", label: "Certificates", icon: ShieldCheck },
    ],
  },
  {
    title: "Operate",
    items: [
      { to: "/discover", label: "Discover system", icon: Radar, role: "admin" },
      { to: "/maintenance", label: "Maintenance", icon: CalendarClock },
      { to: "/rules", label: "Alert rules", icon: SlidersHorizontal },
      { to: "/alerts", label: "Notifications", icon: BellRing },
    ],
  },
  {
    title: "Administration",
    items: [
      { to: "/users", label: "Users & access", icon: Users, role: "admin" },
      { to: "/audit", label: "Audit log", icon: FileClock, role: "admin" },
    ],
  },
];

const TITLES: Record<string, string> = {
  "/": "Overview", "/analytics": "Analytics", "/intelligence": "Intelligence", "/incidents": "Incidents",
  "/landscapes": "Landscapes", "/systems": "SAP systems", "/discover": "Discover system", "/certificates": "Certificates",
  "/maintenance": "Maintenance", "/rules": "Alert rules", "/alerts": "Notifications", "/users": "Users & access",
  "/audit": "Audit log", "/account": "Account",
};

function crumbs(pathname: string): { label: string; to?: string }[] {
  if (pathname.startsWith("/systems/")) return [{ label: "SAP systems", to: "/systems" }, { label: "System" }];
  if (pathname.startsWith("/incidents/")) return [{ label: "Incidents", to: "/incidents" }, { label: "Incident" }];
  if (pathname.startsWith("/landscapes/")) {
    return [{ label: "Landscapes", to: "/landscapes" }, { label: decodeURIComponent(pathname.split("/")[2] ?? "") }];
  }
  return [{ label: TITLES[pathname] ?? "Sentinel" }];
}

function useIncidentCounts() {
  const [counts, setCounts] = useState<IncidentCounts | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.incidentCounts().then((c) => alive && setCounts(c)).catch(() => {});
    load();
    const timer = window.setInterval(load, 60000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);
  return counts;
}

function Brand({ collapsed }: { collapsed: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2.5 px-1 text-sidebar-foreground hover:no-underline">
      <svg viewBox="0 0 36 36" className="h-9 w-9 shrink-0" aria-hidden="true">
        <circle cx="18" cy="18" r="14" fill="none" stroke="hsl(var(--primary))" strokeWidth="4.5" />
        <circle cx="18" cy="18" r="5" fill="hsl(var(--foreground))" />
        <path d="M18 4a14 14 0 0 1 14 14" fill="none" stroke="hsl(var(--foreground))" strokeWidth="4.5" strokeLinecap="round" />
      </svg>
      {!collapsed && <span className="text-[22px] font-bold tracking-[-0.02em]">Sentinel</span>}
    </Link>
  );
}

function SidebarNav({ collapsed, counts, onNavigate }: { collapsed: boolean; counts: IncidentCounts | null; onNavigate?: () => void }) {
  const { can } = useSession();
  return (
    <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-3" aria-label="Main navigation">
      {NAV.map((group) => {
        const items = group.items.filter((i) => !i.role || can(i.role));
        if (!items.length) return null;
        return (
          <div key={group.title}>
            {!collapsed && <div className="mb-1.5 px-4 text-xs font-medium text-sidebar-muted">{group.title}</div>}
            <ul className="space-y-1">
              {items.map((item) => {
                const Icon = item.icon;
                const count = item.badge ? counts?.total ?? 0 : 0;
                const link = (
                  <NavLink to={item.to} end={item.end} onClick={onNavigate}
                    className={({ isActive }) => cn(
                      "group relative flex h-11 items-center gap-3 rounded-full px-4 text-sm font-semibold text-sidebar-foreground/80 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground hover:no-underline",
                      isActive && "bg-primary text-primary-foreground shadow-[0_10px_24px_-10px_hsl(var(--primary)/0.9)] hover:bg-primary hover:text-primary-foreground",
                      collapsed && "justify-center px-0",
                    )}>
                    {({ isActive }) => (<>
                      <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
                      {!collapsed && <span className="truncate">{item.label}</span>}
                      {count > 0 && (
                        <span className={cn("ml-auto flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-bold tabular-nums",
                          isActive ? "bg-white/25 text-white" : counts?.critical ? "bg-bad text-white" : "bg-primary text-primary-foreground",
                          collapsed && "absolute -right-0.5 -top-0.5 ml-0 h-4 min-w-4 px-1 text-[10px]")}>
                          {count}
                        </span>
                      )}
                      {isActive && !collapsed && !count && <ChevronRight className="ml-auto h-4 w-4" aria-hidden="true" />}
                    </>)}
                  </NavLink>
                );
                return (
                  <li key={item.to}>
                    {collapsed ? (
                      <Tooltip>
                        <TooltipTrigger asChild>{link}</TooltipTrigger>
                        <TooltipContent side="right">{item.label}</TooltipContent>
                      </Tooltip>
                    ) : link}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function UserCard({ collapsed }: { collapsed: boolean }) {
  const { me } = useSession();
  const initials = (me?.display_name || me?.username || "?").split(/\s+/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();
  return (
    <Link to="/account" className={cn("mx-3 flex items-center gap-3 rounded-full p-1.5 pr-3 text-sidebar-foreground hover:bg-sidebar-accent hover:no-underline", collapsed && "mx-auto pr-1.5")}>
      <span className="relative">
        <Avatar><AvatarFallback>{initials}</AvatarFallback></Avatar>
        <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-sidebar bg-good" aria-hidden="true" />
      </span>
      {!collapsed && (
        <span className="min-w-0 leading-tight">
          <span className="block truncate text-sm font-bold">{me?.display_name || me?.username || "…"}</span>
          <span className="block truncate text-xs capitalize text-sidebar-muted">{me?.role}{me?.kind === "apikey" ? " · API key" : ""}</span>
        </span>
      )}
    </Link>
  );
}

function ThemeSwitch({ collapsed }: { collapsed: boolean }) {
  const { dark, setTheme } = useTheme();
  if (collapsed) {
    return (
      <button onClick={() => setTheme(dark ? "light" : "dark")} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
        className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-sidebar-accent text-sidebar-foreground">
        {dark ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
      </button>
    );
  }
  return (
    <div className="mx-3 grid grid-cols-2 rounded-full bg-sidebar-accent p-1" role="radiogroup" aria-label="Theme">
      {([["light", Sun, "Light"], ["dark", Moon, "Dark"]] as const).map(([t, Icon, label]) => {
        const on = (t === "dark") === dark;
        return (
          <button key={t} role="radio" aria-checked={on} onClick={() => setTheme(t)}
            className={cn("flex h-9 items-center justify-center gap-2 rounded-full text-sm font-semibold transition-colors",
              on ? "bg-foreground text-background shadow" : "text-sidebar-muted hover:text-sidebar-foreground")}>
            <Icon className="h-4 w-4" />{label}
          </button>
        );
      })}
    </div>
  );
}

function CommandMenu({ open, setOpen }: { open: boolean; setOpen: (o: boolean) => void }) {
  const navigate = useNavigate();
  const { can } = useSession();
  const [systems, setSystems] = useState<SapSystem[]>([]);
  useEffect(() => {
    if (open && !systems.length) api.systems().then(setSystems).catch(() => {});
  }, [open, systems.length]);
  const go = (to: string) => { setOpen(false); navigate(to); };
  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Jump to a system, SID or page…" />
      <CommandList>
        <CommandEmpty>No match. Try a SID such as S4P.</CommandEmpty>
        {systems.length > 0 && (
          <CommandGroup heading="SAP systems">
            {systems.map((s) => (
              <CommandItem key={s.id} value={`${s.sid} ${s.name} ${s.landscape} ${s.environment}`} onSelect={() => go(`/systems/${s.id}`)}>
                <Server />
                <span className="w-12 font-mono font-semibold">{s.sid}</span>
                <span className="truncate">{s.name}</span>
                <span className="ml-auto text-xs text-muted-foreground">{s.environment} · {s.landscape}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        <CommandGroup heading="Pages">
          {NAV.flatMap((g) => g.items).filter((i) => !i.role || can(i.role)).map((i) => (
            <CommandItem key={i.to} value={`page ${i.label}`} onSelect={() => go(i.to)}>
              <i.icon />{i.label}
            </CommandItem>
          ))}
          <CommandItem value="page account password" onSelect={() => go("/account")}><KeyRound />Account &amp; password</CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}

const THEME_ICON: Record<Theme, typeof Sun> = { light: Sun, dark: Moon, system: Monitor };

export function AppShell({ children }: { children: ReactNode }) {
  const { me, logout } = useSession();
  const location = useLocation();
  const counts = useIncidentCounts();
  const { theme, setTheme } = useTheme();
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem("sentinel.nav") === "collapsed"; } catch { return false; }
  });
  const [mobileOpen, setMobileOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const trail = useMemo(() => crumbs(location.pathname), [location.pathname]);

  useEffect(() => {
    try { localStorage.setItem("sentinel.nav", collapsed ? "collapsed" : "expanded"); } catch { /* ignore */ }
  }, [collapsed]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setSearchOpen((o) => !o); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { document.title = `${trail[trail.length - 1].label} · Sentinel`; }, [trail]);

  const ThemeIcon = THEME_ICON[theme];

  return (
    <div className="flex min-h-screen bg-background">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[60] focus:rounded-md focus:bg-card focus:px-3 focus:py-2">Skip to content</a>
      {/* Desktop rail */}
      <aside className={cn("sticky top-0 hidden h-screen shrink-0 flex-col bg-sidebar pb-4 text-sidebar-foreground transition-[width] duration-200 lg:flex",
        collapsed ? "w-[84px]" : "w-[264px]")}>
        <div className={cn("flex h-20 items-center justify-between px-5", collapsed && "justify-center px-0")}>
          <Brand collapsed={collapsed} />
          {!collapsed && (
            <button onClick={() => setCollapsed(true)} aria-label="Collapse navigation"
              className="rounded-lg p-1.5 text-sidebar-muted hover:bg-sidebar-accent hover:text-sidebar-foreground"><PanelLeft className="h-4 w-4" /></button>
          )}
        </div>
        {collapsed && (
          <button onClick={() => setCollapsed(false)} aria-label="Expand navigation"
            className="mx-auto mb-1 rounded-lg p-1.5 text-sidebar-muted hover:bg-sidebar-accent hover:text-sidebar-foreground"><PanelLeft className="h-4 w-4" /></button>
        )}
        <SidebarNav collapsed={collapsed} counts={counts} />
        <div className="space-y-3 pt-3">
          <UserCard collapsed={collapsed} />
          <ThemeSwitch collapsed={collapsed} />
        </div>
      </aside>

      {/* Mobile drawer */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent className="flex flex-col border-0 bg-sidebar p-0 pb-4 text-sidebar-foreground">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <div className="flex h-20 items-center px-5"><Brand collapsed={false} /></div>
          <SidebarNav collapsed={false} counts={counts} onNavigate={() => setMobileOpen(false)} />
          <div className="space-y-3 pt-3"><UserCard collapsed={false} /><ThemeSwitch collapsed={false} /></div>
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-20 items-center gap-3 bg-background/80 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/70 lg:px-8">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open navigation">
            <Menu />
          </Button>
          <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-sm">
            {trail.map((c, i) => (
              <span key={i} className="flex min-w-0 items-center gap-1.5">
                {i > 0 && <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/60" />}
                {c.to ? <Link to={c.to} className="truncate text-muted-foreground hover:text-foreground hover:no-underline">{c.label}</Link>
                  : <span className="truncate font-semibold">{c.label}</span>}
              </span>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2.5">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="dark" size="icon" asChild aria-label="Incidents">
                  <Link to="/incidents" className="relative hover:no-underline">
                    <Bell />
                    {!!counts?.total && (
                      <span className={cn("absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[10px] font-bold text-white ring-2 ring-background",
                        counts.critical ? "bg-bad" : "bg-primary")}>{counts.total > 99 ? "99+" : counts.total}</span>
                    )}
                  </Link>
                </Button>
              </TooltipTrigger>
              <TooltipContent>{counts?.total ? `${counts.total} open incidents${counts.critical ? `, ${counts.critical} critical` : ""}` : "No open incidents"}</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="dark" size="icon" onClick={() => setSearchOpen(true)} aria-label="Search (Ctrl+K)"><Search /></Button>
              </TooltipTrigger>
              <TooltipContent>Search systems and pages <kbd className="ml-1 font-mono">⌘K</kbd></TooltipContent>
            </Tooltip>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="dark" size="icon" aria-label="Account menu"><UserRound /></Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel className="font-normal">
                  <div className="text-sm font-semibold">{me?.display_name || me?.username}</div>
                  <div className="text-xs capitalize text-muted-foreground">{me?.role}{me?.kind === "apikey" ? " · API key" : ""}</div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild><Link to="/account" className="hover:no-underline"><UserCog />Account &amp; password</Link></DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setTheme(theme === "dark" ? "light" : "dark")}><ThemeIcon />Toggle theme</DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={logout}><LogOut />Sign out</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        <main id="main" className="mx-auto w-full min-w-0 max-w-[1480px] flex-1 px-4 pb-10 pt-2 lg:px-8">
          {children}
        </main>
      </div>
      <CommandMenu open={searchOpen} setOpen={setSearchOpen} />
    </div>
  );
}
