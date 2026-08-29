import React, { useEffect, useState } from 'react';
import { api } from '../api/client';
import { Mail, Plus, RefreshCw, Trash2, AlertCircle, CheckCircle2, Clock } from 'lucide-react';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { useNavigate } from 'react-router-dom';

export default function MailScreen() {
  const navigate = useNavigate();
  const [accounts, setAccounts] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Poll for account status (especially if syncing)
  useEffect(() => {
    let active = true;
    const fetchAccounts = async () => {
      try {
        const data = await api.mail.accounts.list();
        if (active) {
          setAccounts(data);
          setError(null);
          setIsLoading(false);
        }
      } catch (err: any) {
        if (active) setError(err.message);
      }
    };
    
    fetchAccounts();
    const interval = setInterval(fetchAccounts, 5000); // Polling every 5s for live sync updates
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const [syncingAccounts, setSyncingAccounts] = useState<Record<string, boolean>>({});

  const handleConnectGmail = async () => {
    try {
      const { url } = await api.mail.oauth.gmailUrl();
      window.location.href = url;
    } catch (err: any) {
      setError(err.detail || err.message || 'Failed to get OAuth URL');
    }
  };

  const handleSync = async (accountId: string) => {
    if (syncingAccounts[accountId]) return;
    setSyncingAccounts(prev => ({ ...prev, [accountId]: true }));
    try {
      await api.mail.accounts.sync(accountId);
      alert('Mail sync started successfully. It will run in the background.');
    } catch (err: any) {
      const msg = String(err.detail || err.message || '');
      if (err.status === 409 || msg.toLowerCase().includes('already in progress') || msg.toLowerCase().includes('sync already')) {
        // Sync is already running smoothly in background
        alert('Mail sync is already running in the background.');
      } else {
        alert(`Sync trigger failed: ${msg}`);
      }
    } finally {
      setTimeout(() => {
        setSyncingAccounts(prev => ({ ...prev, [accountId]: false }));
      }, 3000);
    }
  };

  const handleDelete = async (accountId: string) => {
    if (!confirm('Are you sure you want to disconnect this account? Synced graph data will remain.')) {
      return;
    }
    try {
      await api.mail.accounts.delete(accountId);
      setAccounts(prev => prev.filter(a => a.id !== accountId));
    } catch (err: any) {
      alert(`Delete failed: ${err.detail || err.message}`);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex-none px-8 py-6 border-b border-hairline bg-white flex items-center justify-between">
        <div>
          <h1 className="text-h2 text-ink">Mail Accounts</h1>
          <p className="mt-1 text-body text-ink-muted">
            Connect organizational mail accounts to automatically ingest threads as graph knowledge.
          </p>
        </div>
        <div>
          <Button onClick={handleConnectGmail} className="gap-2">
            <Plus className="w-4 h-4" />
            Connect Gmail
          </Button>
        </div>
      </header>

      <main className="flex-1 overflow-auto p-8 bg-paper">
        {error && (
          <div className="mb-6 rounded-md bg-[#fff1f2] p-4 text-[#be123c] border border-[#fecdd3]">
            {error}
          </div>
        )}

        {isLoading ? (
          <div className="flex items-center gap-2 text-ink-muted">
            <RefreshCw className="w-4 h-4 animate-spin" /> Loading accounts...
          </div>
        ) : accounts.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-12 text-center border border-dashed border-hairline rounded-lg bg-white">
            <Mail className="w-12 h-12 text-ink-muted mb-4 opacity-50" />
            <h3 className="text-h4 text-ink mb-2">No Connected Accounts</h3>
            <p className="text-body text-ink-muted max-w-md">
              Connect a Gmail account to start ingesting email threads into the knowledge graph. 
              Syncs are completely manual – you choose when to pull in new data.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
            {accounts.map(account => (
              <div key={account.id} className="rounded-lg border border-hairline bg-white shadow-sm overflow-hidden flex flex-col">
                <div className="p-5 border-b border-hairline flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex items-center justify-center w-10 h-10 rounded-full bg-[#f8fafc] border border-hairline">
                      <Mail className="w-5 h-5 text-ink-muted" />
                    </div>
                    <div>
                      <h3 className="text-[14px] font-semibold text-ink leading-tight">
                        {account.display_name}
                      </h3>
                      <div className="text-caption text-ink-muted mt-0.5">
                        {account.email_address}
                      </div>
                    </div>
                  </div>
                  <div>
                    {account.status === 'ERROR' ? (
                      <Badge status="alert">Error</Badge>
                    ) : (
                      <Badge status="confirmed">Connected</Badge>
                    )}
                  </div>
                </div>

                <div className="p-5 flex-1 bg-[#fcfcfb]">
                  <div className="space-y-4">
                    <div>
                      <div className="text-caption text-ink-muted mb-1">Provider</div>
                      <div className="text-body capitalize">{account.provider}</div>
                    </div>
                    
                    <div>
                      <div className="text-caption text-ink-muted mb-1">Last Synced</div>
                      <div className="text-body flex items-center gap-1.5">
                        <Clock className="w-3.5 h-3.5 text-ink-muted" />
                        {account.last_synced_at 
                          ? new Date(account.last_synced_at).toLocaleString() 
                          : 'Never synced'}
                      </div>
                    </div>
                    
                    {account.error_message && (
                      <div className="text-caption text-[#e11d48] bg-[#fff1f2] p-2 rounded border border-[#fecdd3]">
                        {account.error_message}
                      </div>
                    )}
                  </div>
                </div>

                <div className="p-4 border-t border-hairline bg-white flex items-center gap-3">
                  <Button 
                    variant="secondary" 
                    className="flex-1 gap-2"
                    disabled={syncingAccounts[account.id] || account.sync_status === 'SYNCING'}
                    onClick={() => handleSync(account.id)}
                  >
                    <RefreshCw className={`w-4 h-4 ${syncingAccounts[account.id] || account.sync_status === 'SYNCING' ? 'animate-spin text-ledger' : ''}`} />
                    {syncingAccounts[account.id] || account.sync_status === 'SYNCING' ? 'Syncing...' : 'Sync Now'}
                  </Button>
                  <Button 
                    variant="danger" 
                    className="px-3"
                    onClick={() => handleDelete(account.id)}
                    title="Disconnect account"
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
