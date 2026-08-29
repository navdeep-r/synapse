import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { ScopeProposal } from '../../api/client';
import { Badge } from '../ui/Badge';
import { CommunityPreviewModal } from './CommunityPreviewModal';
import { DiscoveryPromptModal } from './DiscoveryPromptModal';
import { Search, Check, Eye, Sparkles } from 'lucide-react';

export function CommunityProposals() {
  const queryClient = useQueryClient();
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [selectedProposal, setSelectedProposal] = useState<ScopeProposal | null>(null);
  const [isAiDiscoveryOpen, setIsAiDiscoveryOpen] = useState(false);

  const { data: proposals = [], isLoading: loadingProposals } = useQuery({
    queryKey: ['proposals'],
    queryFn: api.scopes.proposals,
  });

  const generateProposalsMutation = useMutation({
    mutationFn: api.scopes.generateProposals,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['proposals'] })
  });

  const approveProposalMutation = useMutation({
    mutationFn: async ({ proposalId, assignments }: { proposalId: string, assignments: any[] }) => {
      // 1. Approve proposal -> creates scope
      const res = await api.scopes.approveProposal(proposalId);
      // 2. If assignments exist, bulk assign them to the new scope
      if (assignments.length > 0 && res.scope_id) {
        await api.scopes.bulkCreateAssignments(res.scope_id, assignments);
      }
      return res;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['proposals'] });
      queryClient.invalidateQueries({ queryKey: ['scopes'] });
      setIsPreviewOpen(false);
      setSelectedProposal(null);
    }
  });

  const handleApproveWithoutAssignment = (proposalId: string) => {
    approveProposalMutation.mutate({ proposalId, assignments: [] });
  };

  const [prompt, setPrompt] = useState('');
  const discoverMutation = useMutation({
    mutationFn: (p: string) => api.scopes.discoverProposals({ prompt: p }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['proposals'] });
      setPrompt('');
    }
  });

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-start mb-6 gap-4">
        <div>
          <h2 className="text-xl font-medium text-ink">Discovered Communities</h2>
          <p className="text-sm text-ink-muted mt-1">
            Discover communities via graph analysis or AI-guided semantic matching.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={() => generateProposalsMutation.mutate()}
            disabled={generateProposalsMutation.isPending}
            className="flex items-center gap-2 bg-hairline border border-hairline hover:border-ledger text-ink px-4 py-2 rounded-md font-medium text-sm transition-colors"
          >
            <Search className="w-4 h-4" />
            {generateProposalsMutation.isPending ? 'Discovering...' : 'Structural'}
          </button>
        </div>
      </div>
      
      <div className="flex gap-2 mb-6">
        <input 
          type="text" 
          placeholder="Discover Communities (e.g. 'authentication systems')" 
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && prompt.trim()) {
              discoverMutation.mutate(prompt.trim());
            }
          }}
          className="flex-1 border border-hairline rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ledger"
        />
        <button
          onClick={() => prompt.trim() && discoverMutation.mutate(prompt.trim())}
          disabled={!prompt.trim() || discoverMutation.isPending}
          className="flex items-center gap-2 bg-ledger hover:bg-ledger/90 text-white px-4 py-2 rounded-md font-medium text-sm transition-colors disabled:opacity-50"
        >
          <Sparkles className="w-4 h-4" />
          {discoverMutation.isPending ? 'Discovering...' : 'Discover'}
        </button>
      </div>

      {loadingProposals ? (
        <div className="py-12 text-center text-ink-muted">Loading proposals...</div>
      ) : proposals.length === 0 ? (
        <div className="py-12 text-center text-ink-muted border border-dashed border-hairline rounded-lg">
          No open proposals found. Click Discover Scopes to analyze the graph.
        </div>
      ) : (
        <div className="grid gap-4">
          {proposals.map(prop => {
            const isAi = prop.discovery_method === 'prompt_llm';
            const aiMeta = typeof prop.source_distribution_json === 'object' ? prop.source_distribution_json : null;
            const relevance = prop.relevance_score ?? aiMeta?.relevance_score;
            const keywords: string[] = prop.matched_keywords ?? aiMeta?.matched_keywords ?? [];
            return (
            <div key={prop.id} className="p-6 border border-hairline rounded-lg bg-white flex flex-col sm:flex-row gap-4 items-start min-h-[120px]">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h3 className="font-medium text-ink line-clamp-1">{prop.suggested_name}</h3>
                  <Badge status="neutral">Proposed</Badge>
                  {isAi && (
                    <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-ledger/10 text-ledger border border-ledger/20">
                      <Sparkles className="w-3 h-3" /> AI
                    </span>
                  )}
                  {relevance !== undefined && (
                    <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-confirmed/10 text-confirmed border border-confirmed/20">
                      {Math.round(relevance * 100)}% match
                    </span>
                  )}
                </div>
                <p className="text-sm text-ink-muted mt-1 line-clamp-2">{prop.description}</p>
                <div className="flex items-center gap-4 mt-3 text-xs font-mono text-ink-muted flex-wrap">
                  <span>{prop.estimated_entity_count} Entities</span>
                  <span>{prop.estimated_edge_count} Edges</span>
                </div>
                {keywords.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {keywords.slice(0, 6).map(kw => (
                      <span key={kw} className="text-xs px-1.5 py-0.5 rounded bg-ledger/10 text-ledger font-mono border border-ledger/15">{kw}</span>
                    ))}
                  </div>
                )}
              </div>
              <div className="flex-shrink-0 mt-2 sm:mt-0 flex items-center gap-2">
                <button
                  onClick={() => handleApproveWithoutAssignment(prop.id)}
                  disabled={approveProposalMutation.isPending && selectedProposal?.id === prop.id}
                  className="whitespace-nowrap flex items-center justify-center gap-2 bg-hairline text-ink hover:text-white hover:bg-ledger/50 px-3 py-2 rounded-md font-medium text-sm transition-colors flex-1 sm:flex-initial"
                >
                  <Check className="w-4 h-4 flex-shrink-0" />
                  <span className="hidden sm:inline">Approve Only</span>
                </button>
                <button
                  onClick={() => {
                    setSelectedProposal(prop);
                    setIsPreviewOpen(true);
                  }}
                  className="whitespace-nowrap flex items-center justify-center gap-2 bg-ledger text-white hover:bg-ledger/90 px-3 py-2 rounded-md font-medium text-sm transition-colors flex-1 sm:flex-initial"
                >
                  <Eye className="w-4 h-4 flex-shrink-0" />
                  <span className="hidden sm:inline">Preview</span>
                  <span className="sm:hidden">Preview</span>
                </button>
              </div>
            </div>
            );
          })}
        </div>
      )}

      <CommunityPreviewModal
        isOpen={isPreviewOpen}
        onClose={() => {
          setIsPreviewOpen(false);
          setSelectedProposal(null);
        }}
        proposal={selectedProposal}
      />

      <DiscoveryPromptModal
        isOpen={isAiDiscoveryOpen}
        onClose={() => setIsAiDiscoveryOpen(false)}
      />
    </div>
  );
}
