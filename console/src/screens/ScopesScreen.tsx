import * as React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { Link } from 'react-router-dom';
import { Badge } from '../components/ui/Badge';
import { RefreshCw, Layers, ExternalLink, Check } from 'lucide-react';
import { RoleManager } from '../components/scopes/RoleManager';
import { TeamManager } from '../components/scopes/TeamManager';
import { MemberManager } from '../components/scopes/MemberManager';
import { CommunityProposals } from '../components/scopes/CommunityProposals';

export default function ScopesScreen() {
  const [activeTab, setActiveTab] = React.useState<'scopes' | 'communities' | 'roles' | 'teams' | 'members'>('scopes');
  const [selectedScopeId, setSelectedScopeId] = React.useState<string | null>(null);

  const queryClient = useQueryClient();

  const { data: scopes = [], isLoading: loadingScopes } = useQuery({
    queryKey: ['scopes'],
    queryFn: api.scopes.list,
  });

  const { data: versions = [], isLoading: loadingVersions } = useQuery({
    queryKey: ['scopeVersions', selectedScopeId],
    queryFn: () => selectedScopeId ? api.scopes.versions(selectedScopeId) : Promise.resolve([]),
    enabled: !!selectedScopeId,
  });

  const approveVersionMutation = useMutation({
    mutationFn: ({ scopeId, versionId }: { scopeId: string, versionId: string }) => 
      api.scopes.approveVersion(scopeId, versionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scopeVersions'] });
      queryClient.invalidateQueries({ queryKey: ['scopes'] });
    }
  });

  const tabs = [
    { id: 'scopes', label: 'Approved Scopes' },
    { id: 'communities', label: 'Communities' },
    { id: 'roles', label: 'Roles' },
    { id: 'teams', label: 'Teams' },
    { id: 'members', label: 'Members' },
  ] as const;

  return (
    <div className="space-y-6">
      <div className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-ink">Access Management</h1>
        <p className="text-ink-muted mt-2">Manage roles, teams, and data access policies through Knowledge Scopes.</p>
      </div>

      <div className="border-b border-hairline mb-6">
        <nav className="-mb-px flex space-x-8 overflow-x-auto">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`whitespace-nowrap pb-4 px-1 border-b-2 font-medium text-sm transition-colors ${
                activeTab === tab.id
                  ? 'border-ledger text-ledger'
                  : 'border-transparent text-ink-muted hover:text-ink hover:border-ledger'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {activeTab === 'roles' && <RoleManager />}
      {activeTab === 'teams' && <TeamManager />}
      {activeTab === 'members' && <MemberManager />}
      {activeTab === 'communities' && <CommunityProposals />}

      {activeTab === 'scopes' && (
        <div className="flex gap-6 h-[calc(100vh-16rem)]">
          {/* Scopes List */}
          <div className="w-1/3 flex flex-col gap-4 overflow-y-auto pr-2">
            {loadingScopes ? (
              <div className="py-12 text-center text-ink-muted">Loading scopes...</div>
            ) : scopes.length === 0 ? (
              <div className="py-12 text-center text-ink-muted border border-dashed border-hairline rounded-lg">
                No scopes defined.
              </div>
            ) : (
              scopes.map(scope => (
                <button
                  key={scope.id}
                  onClick={() => setSelectedScopeId(scope.id)}
                  className={`text-left p-4 rounded-lg border transition-colors ${
                    selectedScopeId === scope.id 
                      ? 'border-ledger bg-ledger/10' 
                      : 'border-hairline bg-white hover:border-ledger'
                  }`}
                >
                  <div className="flex justify-between items-start mb-2">
                    <h3 className="font-medium text-ink">{scope.name}</h3>
                    {scope.review_status === 'candidate_pending' && (
                      <Badge status="alert">Drifted</Badge>
                    )}
                  </div>
                  <p className="text-sm text-ink-muted line-clamp-2">{scope.description}</p>
                  <div className="mt-3 flex items-center gap-2">
                    <Badge status="neutral">{scope.sensitivity}</Badge>
                    {scope.status === 'deprecated' && <Badge status="alert">Deprecated</Badge>}
                  </div>
                </button>
              ))
            )}
          </div>

          {/* Scope Versions */}
          <div className="w-2/3">
            {selectedScopeId ? (
              <div className="p-6 border border-hairline rounded-lg bg-white flex flex-col h-full overflow-hidden">
                <div className="flex justify-between items-center mb-6 shrink-0">
                  <h2 className="text-xl font-medium text-ink flex items-center gap-2">
                    <Layers className="w-5 h-5 text-ink-muted" />
                    Scope Versions
                  </h2>
                </div>
                
                <div className="flex-grow overflow-y-auto space-y-4 pr-2">
                  {loadingVersions ? (
                    <div className="py-12 text-center text-ink-muted">Loading versions...</div>
                  ) : versions.length === 0 ? (
                    <div className="py-12 text-center text-ink-muted">No versions found.</div>
                  ) : (
                    versions.map(v => (
                      <div key={v.id} className="border border-hairline rounded-lg p-4 flex justify-between items-center bg-paper">
                        <div>
                          <div className="flex items-center gap-3 mb-1">
                            <span className="font-medium text-ink">Version {v.version_number}</span>
                            {v.status === 'approved' && <Badge status="confirmed">Active</Badge>}
                            {v.status === 'candidate' && <Badge status="alert">Candidate</Badge>}
                            {v.status === 'rejected' && <Badge status="alert">Rejected</Badge>}
                          </div>
                          <div className="text-xs text-ink-muted font-mono flex items-center gap-4 mt-2">
                            <span>{v.member_entity_count} Entities</span>
                            <span>{v.member_edge_count} Edges</span>
                            <span className="flex items-center gap-1">
                              <RefreshCw className="w-3 h-3" />
                              Drift: {v.entity_drift_pct.toFixed(1)}% Ent, {v.edge_drift_pct.toFixed(1)}% Edg
                            </span>
                          </div>
                        </div>
                        
                        <div className="flex items-center gap-2">
                          <Link
                            to={`/viewer?scope_version_id=${v.id}`}
                            className="bg-hairline border border-ledger text-ink hover:text-white hover:bg-ledger/50 px-3 py-1.5 rounded text-sm font-medium flex items-center gap-1 transition-colors"
                          >
                            <ExternalLink className="w-4 h-4" />
                            View Graph
                          </Link>
                          {v.status === 'candidate' && (
                            <button 
                              onClick={() => approveVersionMutation.mutate({ scopeId: selectedScopeId, versionId: v.id })}
                              className="bg-ledger text-white hover:bg-ledger/90 px-3 py-1.5 rounded text-sm font-medium flex items-center gap-1 transition-colors"
                            >
                              <Check className="w-4 h-4" />
                              Approve
                            </button>
                          )}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-center h-full text-ink-muted border border-dashed border-hairline rounded-lg bg-white/50 p-12 text-center">
                Select a scope to view its versions and resolve drift candidates.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
