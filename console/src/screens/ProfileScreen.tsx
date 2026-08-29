import { useState, useEffect } from 'react';
import { api } from '../api/client';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { useNavigate } from 'react-router-dom';
import { User, Activity, Shield, Mail, LogOut } from 'lucide-react';

interface Session {
  id: string;
  issued_at: string;
  expires_at: string;
  revoked_at: string | null;
  user_agent: string;
  ip_hash: string;
}

import type { UserInfo } from '../api/client';

export default function ProfileScreen() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'sessions' | 'account' | 'access'>('sessions');
  const navigate = useNavigate();

  const handleSignOut = async () => {
    try {
      await api.auth.logout();
    } catch {
      // ignore
    } finally {
      localStorage.removeItem('synapse_access_token');
      navigate('/login');
    }
  };

  const fetchSessions = async () => {
    try {
      const data = await api.auth.sessions();
      setSessions(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const fetchUserInfo = async () => {
    try {
      const data = await api.auth.me();
      setUserInfo(data);
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchSessions();
    fetchUserInfo();
  }, []);

  const handleRevoke = async (id: string) => {
    try {
      await api.auth.deleteSession(id);
      await fetchSessions();
    } catch (e) {
      console.error("Failed to revoke session", e);
    }
  };

  const handleRevokeAllOther = async () => {
    try {
      await api.auth.deleteAllSessions();
      await fetchSessions();
    } catch (e) {
      console.error("Failed to revoke sessions", e);
    }
  };

  const clearanceColors = {
    public: 'text-green-600 bg-green-50',
    internal: 'text-blue-600 bg-blue-50',
    confidential: 'text-amber-600 bg-amber-50',
    restricted: 'text-red-600 bg-red-50',
  };

  if (loading) return <div className="flex items-center justify-center h-64">Loading...</div>;

  return (
    <div className="space-y-6">
      {/* Tab Navigation */}
      <div className="flex border-b border-hairline">
        {(['sessions', 'account', 'access'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-3 font-medium text-body border-b-2 transition-colors ${
              activeTab === tab 
                ? 'border-ledger text-ledger' 
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {tab === 'sessions' && <Activity className="mr-2 h-4 w-4 inline-block" />}
            {tab === 'account' && <User className="mr-2 h-4 w-4 inline-block" />}
            {tab === 'access' && <Shield className="mr-2 h-4 w-4 inline-block" />}
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {activeTab === 'sessions' && (
        <div className="bg-white border border-hairline rounded-lg p-6 shadow-sm">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold text-ink flex items-center gap-2">
              <Activity className="h-5 w-5 text-ledger" />
              Active Sessions
            </h2>
            <Button variant="secondary" onClick={handleRevokeAllOther} disabled={sessions.filter(s => !s.revoked_at).length <= 1}>
              Revoke All Other Sessions
            </Button>
          </div>
          
          <div className="space-y-4">
            {sessions.length === 0 ? (
              <div className="text-center py-8 text-ink-muted">No sessions found</div>
            ) : (
              sessions.map(s => (
                <div key={s.id} className="flex justify-between items-center p-4 bg-paper border border-hairline rounded-md">
                  <div>
                    <p className="font-medium text-ink">{s.user_agent || 'Unknown Device'}</p>
                    <p className="text-sm text-ink-muted">IP: {s.ip_hash} • Issued: {new Date(s.issued_at).toLocaleString()}</p>
                    {s.revoked_at && <span className="text-xs text-alert font-semibold mt-1 block">REVOKED: {new Date(s.revoked_at).toLocaleString()}</span>}
                  </div>
                  {!s.revoked_at && (
                    <Button variant="ghost" className="text-alert hover:text-alert/80 hover:bg-alert/10" onClick={() => handleRevoke(s.id)}>
                      Revoke
                    </Button>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {activeTab === 'account' && (
        userInfo ? (
          <div className="space-y-6">
          <div className="bg-white border border-hairline rounded-lg p-6 shadow-sm">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-lg font-semibold text-ink flex items-center gap-2">
                <User className="h-5 w-5 text-ledger" />
                Account Information
              </h2>
              <Button variant="ghost" onClick={handleSignOut} className="text-alert hover:text-alert/80 hover:bg-alert/10">
                <LogOut className="h-4 w-4 mr-2" />
                Sign Out
              </Button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div>
                <label className="text-caption text-ink-muted uppercase tracking-wider">Display Name</label>
                <p className="text-body font-medium text-ink mt-1">{userInfo.display_name || '—'}</p>
              </div>
              <div>
                <label className="text-caption text-ink-muted uppercase tracking-wider">Email</label>
                <p className="text-body font-medium text-ink mt-1 flex items-center gap-2">
                  <Mail className="h-4 w-4 text-ink-muted" />
                  {userInfo.email || '—'}
                </p>
              </div>
              <div>
                <label className="text-caption text-ink-muted uppercase tracking-wider">User ID</label>
                <p className="text-body font-mono text-ink mt-1">{userInfo.user_id || '—'}</p>
              </div>
              <div>
                <label className="text-caption text-ink-muted uppercase tracking-wider">Organization</label>
                <p className="text-body font-medium text-ink mt-1">{userInfo.org_id || '—'}</p>
              </div>
              <div>
                <label className="text-caption text-ink-muted uppercase tracking-wider">Token Version</label>
                <p className="text-body font-mono text-ink mt-1">{userInfo.token_version ?? '—'}</p>
              </div>
              <div>
                <label className="text-caption text-ink-muted uppercase tracking-wider">Session ID</label>
                <p className="text-body font-mono text-ink mt-1">{userInfo.session_id || '—'}</p>
              </div>
            </div>
          </div>

          <div className="bg-white border border-hairline rounded-lg p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-ink mb-4 flex items-center gap-2">
              <Shield className="h-5 w-5 text-ledger" />
              Roles & Clearance
            </h2>
            <div className="space-y-4">
              <div className="flex items-center gap-4">
                <span className={`px-2 py-1 rounded text-xs font-semibold ${clearanceColors[(userInfo.clearance || 'public') as keyof typeof clearanceColors] || 'text-ink bg-paper'}`}>
                  {(userInfo.clearance || 'public').toUpperCase()}
                </span>
                <span className="text-caption text-ink-muted">Clearance Level</span>
              </div>
              {userInfo.is_org_supervisor && (
                <Badge status="alert">Organization Supervisor</Badge>
              )}
              <div>
                <label className="text-caption text-ink-muted uppercase tracking-wider mb-1 block">Assigned Roles</label>
                <div className="flex flex-wrap gap-2">
                  {userInfo.role_ids && userInfo.role_ids.length > 0 ? (
                    userInfo.role_ids.map(roleId => (
                      <Badge key={roleId} status="neutral">{roleId}</Badge>
                    ))
                  ) : (
                    <span className="text-caption text-ink-muted">No roles assigned</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
        ) : (
          <div className="text-center py-12 text-ink-muted bg-white shadow-sm border border-hairline rounded-lg">
            Failed to load account information. Please ensure the backend is running and refresh the page.
          </div>
        )
      )}

      {activeTab === 'access' && (
        userInfo ? (
          <div className="space-y-6">
          <div className="bg-white border border-hairline rounded-lg p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-ink mb-4 flex items-center gap-2">
              <Shield className="h-5 w-5 text-ledger" />
              Assigned Scopes
            </h2>
            <div className="space-y-4">
              {userInfo.active_scope_ids && userInfo.active_scope_ids.length > 0 ? (
                userInfo.active_scope_ids.map((scopeId, idx) => (
                  <div key={scopeId} className="flex items-center justify-between p-4 bg-paper border border-hairline rounded-md">
                    <div className="flex items-center gap-3">
                      <Badge status="confirmed">{scopeId}</Badge>
                      <div>
                        <p className="font-medium text-ink">Scope Version</p>
                        <p className="text-sm text-ink-muted">{userInfo.active_scope_version_ids?.[idx] || '—'}</p>
                      </div>
                    </div>
                    <Badge status="confirmed">Active</Badge>
                  </div>
                ))
              ) : (
                <div className="text-center py-8 text-ink-muted">
                  No scopes assigned. Contact your supervisor for access.
                </div>
              )}
            </div>
          </div>
        </div>
        ) : (
          <div className="text-center py-12 text-ink-muted bg-white shadow-sm border border-hairline rounded-lg">
            Failed to load access information. Please ensure the backend is running and refresh the page.
          </div>
        )
      )}
    </div>
  );
}
