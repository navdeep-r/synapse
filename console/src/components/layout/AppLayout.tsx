import * as React from 'react';
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom';
import { 
  Database, 
  Network, 
  Globe, 
  ListChecks, 
  Box, 
  Activity, 
  Settings,
  ChevronLeft,
  ChevronRight,
  MessageSquare,
  Shield,
  LogOut,
  ChevronDown,
  Mail
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Badge } from '../ui/Badge';
import { useProvenance } from '../provenance/ProvenanceContext';
import { ProvenanceRail } from '../provenance/ProvenanceRail';
import { api } from '../../api/client';

interface NavItem {
  label: string;
  icon: LucideIcon;
  path: string;
  badge?: number;
  badgeAlert?: boolean;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

export function AppLayout() {
  const navigate = useNavigate();
  const [navCollapsed, setNavCollapsed] = React.useState(false);
  const [showUserMenu, setShowUserMenu] = React.useState(false);
  const { isOpen: isProvenanceOpen } = useProvenance();

  const [curationBadgeCount, setCurationBadgeCount] = React.useState(0);
  const [snapshotBadgeCount, setSnapshotBadgeCount] = React.useState(0);
  const [snapshotBadgeAlert, setSnapshotBadgeAlert] = React.useState(false);
  const [user, setUser] = React.useState<any>(null);
  const location = useLocation();
  const isChat = location.pathname.startsWith('/chat');

  const loadUser = React.useCallback(async () => {
    try {
      const data = await api.auth.me();
      setUser(data);
    } catch (e: any) {
      if (e?.status === 401) {
        localStorage.removeItem('synapse_access_token');
        navigate('/login');
      }
    }
  }, [navigate]);

  React.useEffect(() => {
    loadUser();
  }, [loadUser]);

  const handleSignOut = async () => {
    try {
      await api.auth.logout();
    } catch {
      // ignore
    } finally {
      localStorage.removeItem('synapse_access_token');
      setUser(null);
      navigate('/login');
    }
  };

  // Close user menu when clicking outside
  React.useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      const target = event.target as HTMLElement;
      if (!target.closest('[data-user-menu]')) {
        setShowUserMenu(false);
      }
    }
    if (showUserMenu) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showUserMenu]);

  // The badges are how a steward learns there is work waiting without opening
  // every screen, so they keep polling rather than reading once on mount.
  React.useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const badges = await api.badges();
        if (cancelled) return;
        setCurationBadgeCount(badges.curation);
        setSnapshotBadgeCount(badges.snapshots);
        setSnapshotBadgeAlert(badges.snapshots_alert);
      } catch {
        /* the sidebar stays usable when the backend is down */
      }
    };

    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const navGroups: NavGroup[] = [
    {
      title: 'Ingestion',
      items: [
        ...(user?.is_org_supervisor ? [{ label: 'Data Sources', icon: Database, path: '/sources' }] : []),
        ...(user?.is_org_supervisor ? [{ label: 'Mail Accounts', icon: Mail, path: '/mail' }] : []),
      ]
    },
    {
      title: 'Knowledge',
      items: [
        { label: 'Ontology', icon: Network, path: '/ontology' },
        { label: 'KG Viewer', icon: Globe, path: '/viewer' },
        { label: 'Knowledge Chat', icon: MessageSquare, path: '/chat' },
      ]
    },
    {
      title: 'Curation',
      items: [
        ...(user?.is_org_supervisor ? [{ label: 'Review Queue', icon: ListChecks, path: '/curation', badge: curationBadgeCount }] : []),
      ]
    },
    {
      title: 'System',
      items: [
        ...(user?.is_org_supervisor ? [{ label: 'Access Control', icon: Shield, path: '/scopes' }] : []),
        { label: 'Snapshot & Export', icon: Box, path: '/export', badge: snapshotBadgeCount, badgeAlert: snapshotBadgeAlert },
        ...(user?.is_org_supervisor ? [{ label: 'Observability', icon: Activity, path: '/observability' }] : []),
        { label: 'Settings', icon: Settings, path: '/settings' },
        { label: 'My Profile', icon: Shield, path: '/profile' },
      ]
    }
  ].filter(group => group.items.length > 0);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-paper">
      
      {/* Left Navigation */}
      <nav 
        className={`flex flex-col border-r border-hairline bg-paper z-20 transition-all duration-300 ease-in-out ${
          navCollapsed ? 'w-[64px]' : 'w-[240px]'
        }`}
      >
        <div className="flex h-20 items-center justify-between px-4 border-b border-hairline shrink-0">
          {!navCollapsed && <span className="font-sans font-bold text-ink">SYNAPSE</span>}
          {navCollapsed && <span className="font-sans font-bold text-ink mx-auto">SYN</span>}
        </div>
        
        <div className="flex-1 overflow-y-auto py-4">
          {navGroups.map((group, idx) => (
            <div key={idx} className="mb-6">
              {!navCollapsed && (
                <div className="px-4 mb-2 text-caption font-medium text-ink-muted uppercase tracking-wider">
                  {group.title}
                </div>
              )}
              <ul className="space-y-1 px-2">
                {group.items.map((item) => (
                  <li key={item.path}>
                    <NavLink
                      to={item.path}
                      className={({ isActive }) => 
                        `flex items-center rounded-md px-2 py-2 transition-colors ${
                          isActive 
                            ? 'bg-ledger text-paper' 
                            : 'text-ink hover:bg-hairline/50'
                        } ${navCollapsed ? 'justify-center' : 'justify-between'}`
                      }
                      title={navCollapsed ? item.label : undefined}
                    >
                      {({ isActive }) => (
                        <>
                          <div className="flex items-center">
                            <item.icon className="h-5 w-5 shrink-0" />
                            {!navCollapsed && <span className="ml-3 text-body">{item.label}</span>}
                          </div>
                          {!navCollapsed && item.badge !== undefined && item.badge > 0 && (
                            <Badge status={isActive ? 'neutral' : (item.badgeAlert ? 'alert' : 'signal')}>
                              {item.badge}
                            </Badge>
                          )}
                        </>
                      )}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="p-2 border-t border-hairline shrink-0">
          {user && (
            <div className="relative" data-user-menu>
              <button
                onClick={() => setShowUserMenu(!showUserMenu)}
                className={`flex w-full items-center ${navCollapsed ? 'justify-center' : 'gap-3'} rounded-md p-2 text-ink hover:bg-hairline hover:text-ink transition-colors`}
                title={navCollapsed ? "User Menu (Sign Out)" : undefined}
              >
                <div className="flex items-center justify-center w-8 h-8 rounded-full bg-ledger text-paper font-medium text-caption shrink-0">
                  {user.display_name?.charAt(0).toUpperCase() || user.email?.charAt(0).toUpperCase()}
                </div>
                {!navCollapsed && (
                  <>
                    <div className="flex-1 min-w-0 text-left">
                      <p className="text-body font-medium truncate">{user.display_name || user.email}</p>
                      <p className="text-caption text-ink-muted truncate">{user.email}</p>
                    </div>
                    <ChevronDown className="h-4 w-4 text-ink-muted shrink-0" />
                  </>
                )}
              </button>
              {showUserMenu && (
                <div className={`absolute bottom-full mb-1 bg-white border border-hairline rounded-md shadow-lg py-1 z-10 ${navCollapsed ? 'left-0 w-48' : 'left-0 right-0'}`}>
                  <button
                    onClick={handleSignOut}
                    className="flex w-full items-center gap-2 px-4 py-2 text-body text-alert hover:bg-alert/10 transition-colors"
                  >
                    <LogOut className="h-4 w-4" />
                    Sign Out
                  </button>
                </div>
              )}
            </div>
          )}

          <div className="p-2 border-t border-hairline shrink-0">
            <button 
              onClick={() => setNavCollapsed(!navCollapsed)}
              className="flex w-full items-center justify-center rounded-md p-2 text-ink-muted hover:bg-hairline hover:text-ink transition-colors"
            >
              {navCollapsed ? <ChevronRight className="h-5 w-5" /> : <ChevronLeft className="h-5 w-5" />}
            </button>
          </div>
        </div>
      </nav>

      {/* Main Content Area */}
      <main 
        className={`flex-1 relative overflow-hidden flex flex-col transition-all duration-300 ${
          isProvenanceOpen ? 'lg:mr-[400px]' : ''
        }`}
      >
        {isChat ? (
          <Outlet />
        ) : (
          <div className="flex-1 overflow-y-auto">
            <div className="mx-auto max-w-content px-6 py-8">
               <Outlet />
            </div>
          </div>
        )}
      </main>

      <ProvenanceRail />
    </div>
  );
}
