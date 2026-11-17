import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  BarChart3, Bell, BellRing, BrainCircuit, CalendarClock, ChevronsLeft, ChevronsRight, FileClock, KeyRound,
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
      <svg viewBox="0 0 32 32" className="h-8 w-8 shrink-0" aria-hidden="true">
        <rect width="32" height="32" rx="8" fill="hsl(var(--primary))" />
        <path d="M8 18.5h4l2.5-6 3 10 2.5-6h4" fill="none" stroke="white" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {!collapsed && (
        <span className="leading-tight">
          <span className="block text-[15px] font-semibold tracking-tight">Sentinel</span>
          <span className="block text-[11px] text-sidebar-muted">SAP Basis operations</span>
        </span>
      )}
    </Link>
  );
}

function SidebarNav({ collapsed, counts, onNavigate }: { collapsed: boolean; counts: IncidentCounts | null; onNavigate?: () => void }) {
  const { can } = useSession();
  return (
    <nav className="flex-1 space-y-5 overflow-y-auto px-2 py-2" aria-label="Main navigation">
      {NAV.map((group) => {
        const items = group.items.filter((i) => !i.role || can(i.role));
        if (!items.length) return null;
        return (
          <div key={group.title}>
            {!collapsed && <div className="mb-1 px-2.5 text-[11px] font-medium text-sidebar-muted">{group.title}</div>}
            <ul className="space-y-0.5">
              {items.map((item) => {
                const Icon = item.icon;
                const count = item.badge ? counts?.total ?? 0 : 0;
                const link = (
                  <NavLink to={item.to} end={item.end} onClick={onNavigate}
                    className={({ isActive }) => cn(
                      "group relative flex h-9 items-center gap-3 rounded-md px-2.5 text-[13.5px] text-sidebar-foreground/75 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground hover:no-underline",
                      isActive && "bg-sidebar-accent font-medium text-sidebar-foreground before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-full before:bg-primary",
                      collapsed && "justify-center px-0",
                    )}>
                    <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
                    {!collapsed && <span className="truncate">{item.label}</span>}
                    {count > 0 && (
                      <span className={cn("ml-auto rounded px-1.5 text-[11px] font-semibold tabular-nums leading-5",
                        counts?.critical ? "bg-bad text-white" : "bg-warn text-white",
                        collapsed && "absolute -right-0.5 -top-0.5 ml-0 px-1 text-[10px] leading-4")}>
                        {count}
                      </span>
                    )}
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

function OpsStatus({ counts, collapsed }: { counts: IncidentCounts | null; collapsed: boolean }) {
  if (!counts) return null;
  const tone = counts.critical ? "bg-bad" : counts.total ? "bg-warn" : "bg-good";
  const text = counts.critical ? `${counts.critical} critical incident${counts.critical === 1 ? "" : "s"}`
    : counts.total ? `${counts.total} open incident${counts.total === 1 ? "" : "s"}` : "No open incidents";
  return (
    <Link to="/incidents" className={cn("mx-2 mb-2 flex items-center gap-2.5 rounded-md border border-sidebar-border px-2.5 py-2 text-xs text-sidebar-foreground/80 hover:bg-sidebar-accent hover:no-underline", collapsed && "justify-center px-0")}>
      <span className={cn("h-2 w-2 shrink-0 rounded-full", tone, !counts.total && "animate-pulse-ring")} aria-hidden="true" />
      {!collapsed && <span>{text}</span>}
      {collapsed && <span className="sr-only">{text}</span>}
    </Link>
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
  const initials = (me?.display_name || me?.username || "?").split(/\s+/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[60] focus:rounded-md focus:bg-card focus:px-3 focus:py-2">Skip to content</a>
      {/* Desktop rail */}
      <aside className={cn("sticky top-0 hidden h-screen shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 lg:flex",
        collapsed ? "w-[64px]" : "w-[248px]")}>
        <div className={cn("flex h-14 items-center px-3", collapsed && "justify-center px-0")}><Brand collapsed={collapsed} /></div>
        <SidebarNav collapsed={collapsed} counts={counts} />
        <OpsStatus counts={counts} collapsed={collapsed} />
        <button onClick={() => setCollapsed((c) => !c)}
          className="mx-2 mb-3 flex h-8 items-center justify-center gap-2 rounded-md text-xs text-sidebar-muted hover:bg-sidebar-accent hover:text-sidebar-foreground"
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}>
          {collapsed ? <ChevronsRight className="h-4 w-4" /> : <><ChevronsLeft className="h-4 w-4" /> Collapse</>}
        </button>
      </aside>

      {/* Mobile drawer */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent className="flex flex-col border-sidebar-border bg-sidebar p-0 text-sidebar-foreground">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <div className="flex h-14 items-center px-3"><Brand collapsed={false} /></div>
          <SidebarNav collapsed={false} counts={counts} onNavigate={() => setMobileOpen(false)} />
          <OpsStatus counts={counts} collapsed={false} />
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/70 lg:px-6">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open navigation">
            <Menu />
          </Button>
          <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-sm">
            {trail.map((c, i) => (
              <span key={i} className="flex min-w-0 items-center gap-1.5">
                {i > 0 && <span className="text-muted-foreground/60">/</span>}
                {c.to ? <Link to={c.to} className="truncate text-muted-foreground hover:text-foreground hover:no-underline">{c.label}</Link>
                  : <span className="truncate font-medium">{c.label}</span>}
              </span>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-1.5">
            <button onClick={() => setSearchOpen(true)}
              className="hidden h-9 w-64 items-center gap-2 rounded-md border border-input bg-card px-3 text-sm text-muted-foreground shadow-sm transition-colors hover:bg-accent md:flex">
              <Search className="h-4 w-4" /> Search systems and pages
              <kbd className="ml-auto rounded border bg-muted px-1.5 font-mono text-[10px]">⌘K</kbd>
            </button>
            <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setSearchOpen(true)} aria-label="Search"><Search /></Button>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon" asChild aria-label="Incidents">
                  <Link to="/incidents" className="relative">
                    <Bell />
                    {!!counts?.total && <span className={cn("absolute right-1.5 top-1.5 h-2 w-2 rounded-full ring-2 ring-background", counts.critical ? "bg-bad" : "bg-warn")} />}
                  </Link>
                </Button>
              </TooltipTrigger>
              <TooltipContent>{counts?.total ? `${counts.total} open incidents` : "No open incidents"}</TooltipContent>
            </Tooltip>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" aria-label={`Theme: ${theme}`}><ThemeIcon /></Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-[9rem]">
                {(["light", "dark", "system"] as Theme[]).map((t) => {
                  const Icon = THEME_ICON[t];
                  return <DropdownMenuItem key={t} onSelect={() => setTheme(t)} className={cn(theme === t && "font-medium")}><Icon />{t[0].toUpperCase() + t.slice(1)}</DropdownMenuItem>;
                })}
              </DropdownMenuContent>
            </DropdownMenu>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="ml-1 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="Account menu">
                  <Avatar><AvatarFallback>{initials}</AvatarFallback></Avatar>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel className="font-normal">
                  <div className="text-sm font-medium">{me?.display_name || me?.username}</div>
                  <div className="text-xs capitalize text-muted-foreground">{me?.role}{me?.kind === "apikey" ? " · API key" : ""}</div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild><Link to="/account" className="hover:no-underline"><UserCog />Account &amp; password</Link></DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={logout}><LogOut />Sign out</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        <main id="main" className="mx-auto w-full min-w-0 max-w-[1440px] flex-1 px-4 py-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
      <CommandMenu open={searchOpen} setOpen={setSearchOpen} />
    </div>
  );
}
