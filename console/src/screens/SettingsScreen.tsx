import * as React from 'react';
import { Settings, Key, Globe, Shield, Trash2 } from 'lucide-react';
import { Button } from '../components/ui/Button';
import { api, type ApiKey, type DeliveryConfig, type GeneralConfig } from '../api/client';

export interface AclGroup {
  id: string;
  name: string;
  description: string;
}

export interface ApiKeyItem {
  id: string;
  name: string;
  maskedKey: string;
  environment: string;
}

export default function SettingsScreen() {
  const [activeTab, setActiveTab] = React.useState<'general' | 'api'>('general');
  const [aclGroups, setAclGroups] = React.useState<AclGroup[]>([]);
  const [apiKeys, setApiKeys] = React.useState<ApiKeyItem[]>([]);
  const [general, setGeneral] = React.useState<GeneralConfig | null>(null);
  const [delivery, setDelivery] = React.useState<DeliveryConfig | null>(null);
  const [newKey, setNewKey] = React.useState<ApiKey | null>(null);
  const [status, setStatus] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const loadAcl = React.useCallback(async () => {
    setAclGroups(await api.settings.aclGroups());
  }, []);

  const loadKeys = React.useCallback(async () => {
    setApiKeys(await api.settings.apiKeys());
  }, []);

  React.useEffect(() => {
    Promise.all([
      api.settings.general().then(setGeneral),
      api.settings.delivery().then(setDelivery),
      loadAcl(),
      loadKeys(),
    ]).catch(e => setError(e instanceof Error ? e.message : 'Failed to load settings'));
  }, [loadAcl, loadKeys]);

  const report = (message: string) => {
    setStatus(message);
    setError(null);
    window.setTimeout(() => setStatus(null), 4000);
  };

  const fail = (e: unknown, fallback: string) =>
    setError(e instanceof Error ? e.message : fallback);

  const saveGeneral = async () => {
    if (!general) return;
    try {
      setGeneral(await api.settings.saveGeneral(general));
      report('General configuration saved.');
    } catch (e) {
      fail(e, 'Failed to save the general configuration');
    }
  };

  const saveDelivery = async () => {
    if (!delivery) return;
    try {
      setDelivery(await api.settings.saveDelivery(delivery));
      report('Delivery configuration saved.');
    } catch (e) {
      fail(e, 'Failed to save the delivery configuration');
    }
  };

  const addGroup = async () => {
    const name = window.prompt('Access group name (for example: finance-restricted)');
    if (!name) return;
    const description = window.prompt('What does this group cover?') ?? '';
    try {
      await api.settings.createAclGroup({ name, description });
      await loadAcl();
      report(`Access group '${name}' created.`);
    } catch (e) {
      fail(e, 'Failed to create the access group');
    }
  };

  const removeGroup = async (id: string) => {
    try {
      await api.settings.deleteAclGroup(id);
      await loadAcl();
    } catch (e) {
      fail(e, 'Failed to remove the access group');
    }
  };

  const generateKey = async () => {
    const name = window.prompt('Name this key (for example: downstream-etl)');
    if (!name) return;
    try {
      const created = await api.settings.createApiKey({ name, environment: 'staging' });
      setNewKey(created);
      await loadKeys();
    } catch (e) {
      fail(e, 'Failed to generate the API key');
    }
  };

  const revokeKey = async (id: string) => {
    try {
      await api.settings.revokeApiKey(id);
      await loadKeys();
      report('Key revoked.');
    } catch (e) {
      fail(e, 'Failed to revoke the key');
    }
  };

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex items-center justify-between shrink-0">
        <h1 className="text-display text-ink tracking-tight flex items-center">
          <Settings className="mr-4 h-8 w-8 text-ledger" />
          Settings
        </h1>
      </div>

      {status && (
        <div className="shrink-0 border border-confirmed/40 bg-confirmed/5 text-confirmed rounded-md px-4 py-3 text-body">
          {status}
        </div>
      )}
      {error && (
        <div className="shrink-0 border border-alert/40 bg-alert/5 text-alert rounded-md px-4 py-3 text-body">
          {error}
        </div>
      )}

      <div className="flex border-b border-hairline shrink-0">
        {(['general', 'api'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-3 font-medium text-body capitalize border-b-2 transition-colors ${
              activeTab === tab 
                ? 'border-ledger text-ledger' 
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {tab === 'general' ? 'General & Access' : 'Export Delivery & API Keys'}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto pb-8">
        {activeTab === 'general' && (
          <div className="max-w-3xl space-y-8">
            
            <div className="border border-hairline rounded-lg bg-white p-6 space-y-4">
              <h2 className="text-h2 text-ink flex items-center"><Globe className="mr-2 h-5 w-5 text-ink-muted" /> General Configuration</h2>
              <div className="grid grid-cols-2 gap-6">
                <div>
                  <label className="block text-caption text-ink-muted font-medium uppercase tracking-wider mb-1">Display Name</label>
                  <input
                    type="text"
                    className="w-full border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger"
                    placeholder="Organization Name"
                    value={general?.display_name ?? ''}
                    onChange={e => setGeneral(t => t && { ...t, display_name: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-caption text-ink-muted font-medium uppercase tracking-wider mb-1">Default Access Tag</label>
                  <select
                    className="w-full border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger"
                    value={general?.default_access_tag ?? 'internal'}
                    onChange={e => setGeneral(t => t && { ...t, default_access_tag: e.target.value })}
                  >
                    <option value="public">public</option>
                    <option value="internal">internal</option>
                    <option value="confidential">confidential</option>
                  </select>
                  <p className="text-caption text-ink-muted mt-1">
                    Applied to uploaded documents that carry no tag of their own.
                  </p>
                </div>
                <div>
                  <label className="block text-caption text-ink-muted font-medium uppercase tracking-wider mb-1">Retention (days)</label>
                  <input
                    type="number"
                    className="w-full border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger"
                    value={general?.retention_days ?? 365}
                    onChange={e => setGeneral(t => t && { ...t, retention_days: Number(e.target.value) })}
                  />
                </div>
              </div>
              <Button variant="primary" onClick={saveGeneral} disabled={!general}>Save Changes</Button>
            </div>

            <div className="border border-hairline rounded-lg bg-white p-6 space-y-4">
              <h2 className="text-h2 text-ink flex items-center"><Shield className="mr-2 h-5 w-5 text-ink-muted" /> Access Control Lists (ACL)</h2>
              <p className="text-body text-ink-muted">Manage the default groups propagated into downstream snapshots when using 'Filter by Access Group'.</p>
              
              <div className="space-y-3">
                {aclGroups.map(group => (
                  <div key={group.id} className="flex justify-between items-center p-3 border border-hairline rounded bg-paper/50">
                    <div>
                      <div className="font-medium text-ink">{group.name}</div>
                      <div className="text-caption text-ink-muted">{group.description}</div>
                    </div>
                    <Button 
                      variant="ghost" 
                      size="sm" 
                      className="text-alert hover:text-alert"
                      onClick={() => removeGroup(group.id)}
                    >
                      Remove
                    </Button>
                  </div>
                ))}
                {aclGroups.length === 0 && (
                  <div className="p-4 text-center text-ink-muted border border-hairline bg-paper/20 rounded italic">
                    No access control groups registered.
                  </div>
                )}
              </div>
              <Button variant="secondary" onClick={addGroup}>Add Group</Button>
            </div>
            
          </div>
        )}

        {activeTab === 'api' && (
          <div className="max-w-3xl space-y-8">
            
            <div className="border border-hairline rounded-lg bg-white p-6 space-y-4">
              <h2 className="text-h2 text-ink flex items-center"><Key className="mr-2 h-5 w-5 text-ink-muted" /> API Keys</h2>
              <p className="text-body text-ink-muted">Keys used by downstream systems to fetch generated snapshots via the Delivery API.</p>

              {newKey?.key && (
                <div className="border border-ledger/40 bg-ledger/5 rounded-md p-4 space-y-2">
                  <div className="font-medium text-ink">Copy this key now — it is not stored and cannot be shown again.</div>
                  <code className="block font-mono text-body text-ink break-all bg-white border border-hairline rounded p-2">
                    {newKey.key}
                  </code>
                  <Button variant="ghost" size="sm" onClick={() => setNewKey(null)}>I have copied it</Button>
                </div>
              )}

              <div className="border border-hairline rounded divide-y divide-hairline">
                 {apiKeys.map(key => (
                   <div key={key.id} className="p-4 flex justify-between items-center">
                     <div>
                       <div className="font-medium text-ink">{key.name} ({key.environment})</div>
                       <div className="text-caption font-mono text-ink-muted mt-1">{key.maskedKey}</div>
                     </div>
                     <div className="flex gap-2">
                       <Button variant="secondary" size="sm" className="text-alert" onClick={() => revokeKey(key.id)}>
                         <Trash2 className="mr-2 h-4 w-4" /> Revoke
                       </Button>
                     </div>
                   </div>
                 ))}
                 {apiKeys.length === 0 && (
                   <div className="p-6 text-center text-ink-muted italic">
                     No active API keys found.
                   </div>
                 )}
              </div>
              <Button variant="primary" onClick={generateKey}>Generate New Key</Button>
            </div>

            <div className="border border-hairline rounded-lg bg-white p-6 space-y-4">
              <h2 className="text-h2 text-ink">Export Delivery Configuration</h2>
              
              <div className="space-y-4">
                <div>
                  <label className="block text-caption text-ink-muted font-medium uppercase tracking-wider mb-1">Webhook URL</label>
                  <p className="text-caption text-ink-muted mb-2">Recorded against each published snapshot. The console does not make outbound requests; downstream systems pull from the delivery API.</p>
                  <input
                    type="url"
                    className="w-full border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger"
                    placeholder="https://api.yourplatform.com/webhooks/ei"
                    value={delivery?.endpoint ?? ''}
                    onChange={e => setDelivery(d => d && { ...d, endpoint: e.target.value })}
                  />
                </div>
                
                <div>
                  <label className="block text-caption text-ink-muted font-medium uppercase tracking-wider mb-1">Payload Format</label>
                  <select
                    className="w-1/3 border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger"
                    value={delivery?.format ?? 'json.gz'}
                    onChange={e => setDelivery(d => d && { ...d, format: e.target.value })}
                  >
                    <option value="json.gz">json.gz</option>
                    <option value="json">json</option>
                  </select>
                </div>
              </div>

              <div className="pt-4">
                <Button variant="primary" onClick={saveDelivery} disabled={!delivery}>Save Configuration</Button>
              </div>
            </div>

          </div>
        )}
      </div>
    </div>
  );
}
