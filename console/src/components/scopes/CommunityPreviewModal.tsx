import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { ScopeProposal } from '../../api/client';
import { GraphPreview } from '../ui/GraphPreview';
import { Badge } from '../ui/Badge';
import { X, Sparkles, Check } from 'lucide-react';
import { AssignmentDropdown, type AssigneeKind } from './AssignmentDropdown';

import { useQueryClient } from '@tanstack/react-query';

interface Props {
  proposal: ScopeProposal | null;
  isOpen: boolean;
  onClose: () => void;
}

export function CommunityPreviewModal({ proposal, isOpen, onClose }: Props) {
  const [graphData, setGraphData] = useState<{ nodes: any[], edges: any[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const queryClient = useQueryClient();
  const [isApproving, setIsApproving] = useState(false);
  const [assignee, setAssignee] = useState<{kind: AssigneeKind, id: string} | null>(null);

  const handleApproveWithAssignment = async () => {
    if (!proposal) return;
    try {
      setIsApproving(true);
      const res = await api.scopes.approveProposal(proposal.id);
      if (assignee?.id && res.scope_id) {
        await api.scopes.createAssignment(res.scope_id, {
          assignee_kind: assignee.kind,
          assignee_id: assignee.id,
          reason: 'Auto-assigned during community approval'
        });
      }
      queryClient.invalidateQueries({ queryKey: ['proposals'] });
      queryClient.invalidateQueries({ queryKey: ['scopes'] });
      onClose();
    } catch (err: any) {
      alert(err.message || "Failed to approve proposal");
    } finally {
      setIsApproving(false);
    }
  };



  useEffect(() => {
    async function loadPreview() {
      if (!proposal || !isOpen) return;
      try {
        setLoading(true);
        setError(null);
        const data = await api.scopes.previewProposal(proposal.id, assignee?.kind, assignee?.id);
        setGraphData(data);
      } catch (err: any) {
        setError(err.message || "Failed to load preview");
        setGraphData(null);
      } finally {
        setLoading(false);
      }
    }
    loadPreview();
  }, [proposal?.id, isOpen, assignee?.id, assignee?.kind]);

  if (!isOpen || !proposal) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white w-full max-w-5xl h-[85vh] rounded-xl shadow-2xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-hairline">
          <div>
            <h2 className="text-xl font-medium text-ink">{proposal.suggested_name}</h2>
            <div className="flex items-center gap-3 mt-1 text-sm text-ink-muted">
              <Badge status="neutral">{proposal.estimated_entity_count} Entities</Badge>
              <Badge status="neutral">{proposal.estimated_edge_count} Edges</Badge>
              <Badge status={proposal.suggested_sensitivity === 'restricted' ? 'alert' : proposal.suggested_sensitivity === 'confidential' ? 'signal' : 'confirmed'}>
                {proposal.suggested_sensitivity}
              </Badge>
              <span>Confidence: {((proposal.confidence || 0) * 100).toFixed(0)}%</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={onClose} className="p-2 text-ink-muted hover:text-ink hover:bg-hairline rounded-md">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 flex overflow-hidden">
          {/* Graph Preview */}
          <div className="flex-1 relative min-w-0">
            {loading && (
              <div className="absolute inset-0 flex items-center justify-center bg-paper/80 z-10">
                <div className="animate-spin rounded-full h-8 w-8 border-2 border-ledger border-t-transparent"></div>
              </div>
            )}
            {error && (
              <div className="absolute inset-0 flex items-center justify-center bg-paper/90 z-10 p-8 text-center">
                <div className="text-ink">
                  <p className="font-medium text-lg mb-2">No Data to Display</p>
                  <p className="text-ink-muted mb-4">{error}</p>
                  <p className="text-sm text-ink-muted">The community boundary may not match any current entities in the graph.</p>
                </div>
              </div>
            )}
            {!loading && !error && graphData?.nodes.length === 0 && (
              <div className="absolute inset-0 flex items-center justify-center bg-paper/90 z-10 p-8 text-center">
                <div className="text-ink">
                  <p className="font-medium text-lg mb-2">No Accessible Data in This Community</p>
                  <p className="text-ink-muted mb-4">
                    You don't have access to any entities in this community based on your current scopes.
                  </p>
                </div>
              </div>
            )}
            {graphData && graphData.nodes.length > 0 && (
              <GraphPreview 
                nodes={graphData.nodes} 
                edges={graphData.edges} 
                width="100%" 
                height="100%"
              />
            )}
          </div>

          {/* Sidebar */}
          <div className="w-80 border-l border-hairline bg-white p-4 overflow-y-auto">
            <h3 className="font-medium text-ink mb-4">Community Details</h3>
            <div className="space-y-3 text-sm">
              <div className="p-3 bg-paper rounded-lg">
                <p className="text-ink-muted text-xs uppercase tracking-wider mb-1">Entities</p>
                <p className="text-ink font-semibold text-lg">{proposal.estimated_entity_count}</p>
              </div>
              <div className="p-3 bg-paper rounded-lg">
                <p className="text-ink-muted text-xs uppercase tracking-wider mb-1">Edges</p>
                <p className="text-ink font-semibold text-lg">{proposal.estimated_edge_count}</p>
              </div>
              <div className="p-3 bg-paper rounded-lg">
                <p className="text-ink-muted text-xs uppercase tracking-wider mb-1">Confidence</p>
                <p className="text-ink font-semibold text-lg">{((proposal.confidence || 0) * 100).toFixed(0)}%</p>
              </div>
              <div className="p-3 bg-paper rounded-lg">
                <p className="text-ink-muted text-xs uppercase tracking-wider mb-1">Suggested Sensitivity</p>
                <Badge status={
                  proposal.suggested_sensitivity === 'restricted' ? 'alert' : 
                  proposal.suggested_sensitivity === 'confidential' ? 'signal' : 'confirmed'
                }>
                  {proposal.suggested_sensitivity}
                </Badge>
              </div>
            </div>

            {/* AI Relevance section — only for prompt_llm proposals */}
            {(() => {
              const isAi = proposal.discovery_method === 'prompt_llm';
              const aiMeta = typeof proposal.source_distribution_json === 'object' ? proposal.source_distribution_json : null;
              const relevance = proposal.relevance_score ?? aiMeta?.relevance_score;
              const keywords: string[] = proposal.matched_keywords ?? aiMeta?.matched_keywords ?? [];
              if (!isAi && relevance === undefined) return null;
              return (
                <div className="mt-4 p-3 bg-ledger/5 rounded-lg border border-ledger/15">
                  <div className="flex items-center gap-1.5 mb-2">
                    <Sparkles className="w-3.5 h-3.5 text-ledger" />
                    <p className="text-xs font-semibold text-ledger uppercase tracking-wider">AI Discovery</p>
                  </div>
                  {relevance !== undefined && (
                    <div className="mb-2">
                      <p className="text-ink-muted text-xs mb-1">Relevance</p>
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-2 bg-hairline rounded-full overflow-hidden">
                          <div
                            className="h-full bg-ledger rounded-full transition-all"
                            style={{ width: `${Math.round(relevance * 100)}%` }}
                          />
                        </div>
                        <span className="text-xs font-semibold text-ink">{Math.round(relevance * 100)}%</span>
                      </div>
                    </div>
                  )}
                  {keywords.length > 0 && (
                    <div>
                      <p className="text-ink-muted text-xs mb-1.5">Matched Keywords</p>
                      <div className="flex flex-wrap gap-1">
                        {keywords.slice(0, 10).map((kw: string) => (
                          <span key={kw} className="text-xs px-1.5 py-0.5 rounded bg-ledger/10 text-ledger font-mono border border-ledger/15">{kw}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })()}

            {proposal.sample_entities_json && (
              <div className="mt-6 pt-4 border-t border-hairline">
                <h4 className="font-medium text-ink mb-3">Sample Entities</h4>
                <div className="space-y-2 max-h-40 overflow-y-auto">
                  {(Array.isArray(proposal.sample_entities_json) ? proposal.sample_entities_json : 
                    (typeof proposal.sample_entities_json === 'string' ? JSON.parse(proposal.sample_entities_json) : [])
                  ).slice(0, 15).map((name: string, i: number) => (
                    <div key={i} className="text-xs text-ink truncate bg-paper px-2 py-1 rounded border border-hairline">{name}</div>
                  ))}
                </div>
              </div>
            )}
            
            <div className="mt-6 pt-4 border-t border-hairline space-y-4">
              <div>
                <h4 className="font-medium text-ink mb-1">Approve Scope</h4>
                <p className="text-sm text-ink-muted leading-relaxed mb-4">
                  Approval creates a new Knowledge Scope based on this community. 
                  You can optionally assign it immediately below.
                </p>
                <AssignmentDropdown 
                  onAssign={(kind, id) => setAssignee({ kind, id })}
                  disabled={isApproving}
                />
              </div>

              <button
                onClick={handleApproveWithAssignment}
                disabled={isApproving}
                className="w-full bg-ledger text-white hover:bg-ledger/90 py-2 rounded-md font-medium text-sm transition-colors flex items-center justify-center gap-2 disabled:opacity-50 mt-4"
              >
                <Check className="w-4 h-4" />
                {isApproving ? 'Approving...' : (assignee?.id ? 'Approve & Assign' : 'Approve Only')}
              </button>
            </div>

            <div className="mt-6 pt-4 border-t border-hairline">
              <p className="text-xs text-ink-muted leading-relaxed">
                This preview represents only the portion of the community that falls within your current access level and active scopes.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
