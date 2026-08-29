import * as React from 'react';
import { Plus, UploadCloud, RefreshCw, Loader2 } from 'lucide-react';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';

import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';

export interface SourceItem {
  id: string;
  type: string;
  last_cdc_run: string;
  status: 'confirmed' | 'signal' | 'alert' | string;
  doc_count: number;
}

export interface ActivityItem {
  id: string;
  timestamp: string;
  event: string;
  source_id: string;
  details: string;
}

export default function DataSourcesScreen() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = React.useState<'sources' | 'upload' | 'activity' | 'github'>('sources');
  const [sources, setSources] = React.useState<SourceItem[]>([]);
  const [activities, setActivities] = React.useState<ActivityItem[]>([]);
  const [globalActivities, setGlobalActivities] = React.useState<ActivityItem[]>([]);
  const [githubRepos, setGithubRepos] = React.useState<any[]>([]);
  const [newRepoOwner, setNewRepoOwner] = React.useState('');
  const [newRepoName, setNewRepoName] = React.useState('');
  const [isAddingRepo, setIsAddingRepo] = React.useState(false);
  const [selectedSource, setSelectedSource] = React.useState<string | null>(null);
  
  const [isUploading, setIsUploading] = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const fetchSources = async () => {
    try {
      // The sources endpoint is actually /sources in the backend but we haven't added it to client.ts. 
      // For now, we'll manually fetch but include the Authorization header to match what the client does.
      const token = localStorage.getItem('synapse_access_token');
      const res = await fetch('/api/v1/sources', {
        headers: token ? { 'Authorization': `Bearer ${token}` } : {}
      });
      if (res.ok) {
        const data = await res.json();
        setSources(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchActivities = async (sourceId: string) => {
    try {
      const token = localStorage.getItem('synapse_access_token');
      const res = await fetch(`/api/v1/sources/${sourceId}/activity`, {
        headers: token ? { 'Authorization': `Bearer ${token}` } : {}
      });
      if (res.ok) {
        const data = await res.json();
        setActivities(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchGlobalActivities = async () => {
    try {
      const token = localStorage.getItem('synapse_access_token');
      const res = await fetch('/api/v1/activity', {
        headers: token ? { 'Authorization': `Bearer ${token}` } : {}
      });
      if (res.ok) {
        const data = await res.json();
        setGlobalActivities(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  React.useEffect(() => {
    fetchSources();
  }, []);

  React.useEffect(() => {
    if (activeTab === 'activity') {
      fetchGlobalActivities();
    } else if (activeTab === 'github') {
      fetchGithubRepos();
    }
  }, [activeTab]);

  const fetchGithubRepos = async () => {
    try {
      const data = await api.github.repos();
      setGithubRepos(data);
    } catch (e) {
      console.error(e);
    }
  };

  const handleSyncGithubRepo = async (repoId: string) => {
    try {
      await api.github.syncRepo(repoId);
      alert('GitHub sync started successfully. It will run in the background.');
    } catch (e) {
      console.error(e);
      alert('Error triggering sync');
    }
  };

  const handleAddGithubRepo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRepoOwner.trim() || !newRepoName.trim()) return;
    
    let finalOwner = newRepoOwner.trim();
    let finalName = newRepoName.trim();
    
    // If the user pasted a full GitHub URL into the repository name field
    if (finalName.includes('github.com/')) {
      try {
        const urlStr = finalName.startsWith('http') ? finalName : `https://${finalName}`;
        const url = new URL(urlStr);
        const parts = url.pathname.split('/').filter(Boolean);
        if (parts.length >= 2) {
          finalOwner = parts[0];
          finalName = parts[1].replace('.git', '');
        }
      } catch (e) {
        // Fallback to original values if URL parsing fails
      }
    }
    
    setIsAddingRepo(true);
    try {
      await api.github.addRepo(finalOwner, finalName);
      setNewRepoOwner('');
      setNewRepoName('');
      fetchGithubRepos();
    } catch (err: any) {
      console.error(err);
      alert(`Failed to add repository: ${err.detail || 'Unknown error'}`);
    } finally {
      setIsAddingRepo(false);
    }
  };

  React.useEffect(() => {
    if (selectedSource) {
      fetchActivities(selectedSource);
    }
  }, [selectedSource]);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    const formData = new FormData();
    formData.append('file', file);

    try {
      const token = localStorage.getItem('synapse_access_token');
      const res = await fetch('/api/v1/uploads', {
        method: 'POST',
        headers: token ? { 'Authorization': `Bearer ${token}` } : {},
        body: formData,
      });
      if (res.ok) {
        navigate('/observability?live=true');
      } else {
        alert('File upload failed');
      }
    } catch (err) {
      console.error(err);
      alert('File upload failed');
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleReplay = async () => {
    if (!selectedSource) return;
    try {
      const token = localStorage.getItem('synapse_access_token');
      const res = await fetch(`/api/v1/sources/${selectedSource}/replay`, { 
        method: 'POST',
        headers: token ? { 'Authorization': `Bearer ${token}` } : {} 
      });
      if (res.ok) {
        alert('Replay triggered');
        fetchActivities(selectedSource);
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-display text-ink tracking-tight">Data Sources & Ingestion</h1>
        <Button variant="primary" onClick={() => setActiveTab('upload')}>
          <Plus className="mr-2 h-4 w-4" /> Add Source
        </Button>
      </div>

      <div className="flex border-b border-hairline">
        {(['sources', 'upload', 'activity', 'github'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-3 font-medium text-body capitalize border-b-2 transition-colors ${
              activeTab === tab 
                ? 'border-ledger text-ledger' 
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {tab.replace('-', ' ')}
          </button>
        ))}
      </div>

      {activeTab === 'sources' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-2 space-y-4">
            <h2 className="text-h2 text-ink">Connected Sources</h2>
            <div className="overflow-hidden rounded-lg border border-hairline bg-white">
              <table className="w-full text-left text-body">
                <thead className="bg-paper border-b border-hairline text-ink-muted text-caption uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 font-medium">Type</th>
                    <th className="px-4 py-3 font-medium">Last CDC Run</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium text-right">Docs</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {sources.map(source => (
                    <tr 
                      key={source.id} 
                      onClick={() => setSelectedSource(source.id)}
                      className={`cursor-pointer transition-colors hover:bg-paper/50 ${selectedSource === source.id ? 'bg-paper' : ''}`}
                    >
                      <td className="px-4 py-3 font-medium text-ink">{source.type}</td>
                      <td className="px-4 py-3 text-ink-muted">{source.last_cdc_run}</td>
                      <td className="px-4 py-3">
                        <Badge status={source.status === 'confirmed' ? 'confirmed' : source.status === 'signal' ? 'signal' : 'alert'}>
                          {source.status === 'confirmed' ? 'Healthy' : source.status === 'signal' ? 'Syncing' : 'Failed'}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-right text-ink-muted">{(source.doc_count || 0).toLocaleString()}</td>
                    </tr>
                  ))}
                  {sources.length === 0 && (
                    <tr>
                      <td colSpan={4} className="px-4 py-8 text-center text-ink-muted italic">
                        No connected data sources configured.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="lg:col-span-1 border-l border-hairline pl-8">
            {selectedSource ? (
              <div className="space-y-6">
                <h2 className="text-h2 text-ink flex justify-between items-center">
                  Source Activity
                  <Button variant="secondary" size="sm" onClick={handleReplay}>
                    <RefreshCw className="mr-2 h-3 w-3" /> Replay Source
                  </Button>
                </h2>
                <div className="space-y-4">
                  {activities.map(activity => (
                    <div key={activity.id || activity.timestamp} className="text-body border-l-2 border-ledger pl-3 py-1">
                      <div className="font-mono text-caption text-ink-muted">{activity.timestamp}</div>
                      <div className="font-medium text-ink">{activity.event}</div>
                      <div className="text-ink-muted">{activity.details}</div>
                    </div>
                  ))}
                  {activities.length === 0 && (
                    <div className="text-ink-muted italic text-body">No recent activity.</div>
                  )}
                </div>
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-ink-muted text-body">
                Select a source to view activity
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'upload' && (
        <div className="max-w-3xl space-y-6">
          <input 
            type="file" 
            ref={fileInputRef} 
            className="hidden" 
            onChange={handleFileUpload} 
          />
          <div 
            onClick={() => !isUploading && fileInputRef.current?.click()}
            className={`border-2 border-dashed border-hairline rounded-lg p-12 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ledger ${isUploading ? 'opacity-50 cursor-not-allowed' : 'hover:bg-paper/50 cursor-pointer'}`}
          >
            {isUploading ? (
               <Loader2 className="mx-auto h-12 w-12 text-ledger animate-spin mb-4" />
            ) : (
               <UploadCloud className="mx-auto h-12 w-12 text-ink-muted mb-4" />
            )}
            
            <h3 className="text-h2 text-ink mb-1">
              {isUploading ? 'Uploading...' : 'Drag and drop files or click to browse'}
            </h3>
            <p className="text-body text-ink-muted">PDF, Word, or plain text up to 50MB each</p>
          </div>

          <div className="space-y-4">
            <h3 className="text-h2 text-ink">Staged Uploads</h3>
            <div className="p-8 text-center text-ink-muted border border-hairline rounded-lg bg-white italic">
              No files currently staged for ingestion.
            </div>
          </div>
        </div>
      )}

      {activeTab === 'activity' && (
        <div className="space-y-4">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-h2 text-ink">Global Ingestion Feed</h2>
            <input type="text" placeholder="Filter by source, event, or status..." className="px-3 py-1.5 border border-hairline rounded-md text-body focus:outline-none focus:ring-2 focus:ring-ledger w-64" />
          </div>
          <div className="overflow-hidden rounded-lg border border-hairline bg-white">
            <table className="w-full text-left text-body">
               <thead className="bg-paper border-b border-hairline text-ink-muted text-caption uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 font-medium">Timestamp</th>
                    <th className="px-4 py-3 font-medium">Source</th>
                    <th className="px-4 py-3 font-medium">Event</th>
                    <th className="px-4 py-3 font-medium">Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {globalActivities.map((act, i) => (
                    <tr key={act.id || i} className="hover:bg-paper/50 transition-colors">
                      <td className="px-4 py-3 font-mono text-ink-muted">{act.timestamp}</td>
                      <td className="px-4 py-3 text-ink font-medium">{act.source_id}</td>
                      <td className="px-4 py-3 text-ink font-medium">{act.event}</td>
                      <td className="px-4 py-3 text-ink-muted text-caption">{act.details}</td>
                    </tr>
                  ))}
                  {globalActivities.length === 0 && (
                    <tr>
                      <td colSpan={4} className="px-4 py-8 text-center text-ink-muted italic">
                        No global activity recorded yet.
                      </td>
                    </tr>
                  )}
                </tbody>
            </table>
          </div>
        </div>
      )}
      {activeTab === 'github' && (
        <div className="space-y-6">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-h2 text-ink">GitHub Repositories</h2>
          </div>
          
          <form onSubmit={handleAddGithubRepo} className="flex gap-4 items-end bg-paper/50 p-6 rounded-lg border border-hairline">
            <div className="flex-1 space-y-1">
              <label className="text-caption text-ink-muted">Owner</label>
              <input 
                type="text" 
                placeholder="e.g. facebook" 
                value={newRepoOwner} 
                onChange={e => setNewRepoOwner(e.target.value)}
                className="w-full px-3 py-2 border border-hairline rounded-md text-body focus:outline-none focus:ring-2 focus:ring-ledger"
              />
            </div>
            <div className="flex-1 space-y-1">
              <label className="text-caption text-ink-muted">Repository Name</label>
              <input 
                type="text" 
                placeholder="e.g. react" 
                value={newRepoName} 
                onChange={e => setNewRepoName(e.target.value)}
                className="w-full px-3 py-2 border border-hairline rounded-md text-body focus:outline-none focus:ring-2 focus:ring-ledger"
              />
            </div>
            <Button variant="primary" type="submit" disabled={isAddingRepo || !newRepoOwner || !newRepoName}>
              {isAddingRepo ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Plus className="h-4 w-4 mr-2" />}
              Add Repository
            </Button>
          </form>

          <div className="mb-4 p-4 border border-ledger bg-paper/30 rounded-lg text-body text-ink">
            <strong>Webhook Configuration:</strong> Configure your GitHub App to send webhooks to <code>{window.location.origin}/api/v1/github/webhook</code> with content type <code>application/json</code> and select "push" events.
          </div>
          <div className="overflow-hidden rounded-lg border border-hairline bg-white">
            <table className="w-full text-left text-body">
               <thead className="bg-paper border-b border-hairline text-ink-muted text-caption uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 font-medium">Repository</th>
                    <th className="px-4 py-3 font-medium">Default Branch</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Last Synced</th>
                    <th className="px-4 py-3 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {githubRepos.map((repo, i) => (
                    <tr key={repo.id || i} className="hover:bg-paper/50 transition-colors">
                      <td className="px-4 py-3 text-ink font-medium">{repo.owner}/{repo.name}</td>
                      <td className="px-4 py-3 text-ink font-mono">{repo.default_branch}</td>
                      <td className="px-4 py-3">
                         <Badge status={repo.status === 'READY' ? 'confirmed' : repo.status === 'ERROR' ? 'alert' : 'signal'}>
                            {repo.status}
                         </Badge>
                      </td>
                      <td className="px-4 py-3 text-ink-muted text-caption">{repo.last_synced_at || 'Never'}</td>
                      <td className="px-4 py-3 text-right">
                         <Button variant="secondary" size="sm" onClick={() => handleSyncGithubRepo(repo.id)}>
                           Sync Now
                         </Button>
                      </td>
                    </tr>
                  ))}
                  {githubRepos.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-ink-muted italic">
                        No GitHub repositories connected. Install the GitHub App to see repositories here.
                      </td>
                    </tr>
                  )}
                </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
