import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { ScopeProposal } from '../../api/client';
import { Sparkles, X, Search, CheckCircle, Eye, AlertCircle, Loader2 } from 'lucide-react';
import { CommunityPreviewModal } from './CommunityPreviewModal';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

const EXAMPLE_PROMPTS = [
  'payment processing and fraud detection',
  'authentication and identity management',
  'data pipeline infrastructure',
];

function RelevanceBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    pct >= 80 ? 'bg-confirmed/15 text-confirmed border-confirmed/30' :
    pct >= 60 ? 'bg-ledger/15 text-ledger border-ledger/30' :
    'bg-hairline text-ink-muted border-hairline';
  return (
    <span className={`inline-flex items-center text-xs font-semibold px-2 py-0.5 rounded-full border ${color}`}>
      {pct}% match
    </span>
  );
}

function KeywordChip({ keyword }: { keyword: string }) {
  return (
    <span className="inline-flex items-center text-xs px-2 py-0.5 rounded bg-ledger/10 text-ledger border border-ledger/20 font-mono">
      {keyword}
    </span>
  );
}

function ProposalCard({
  proposal,
  onApprove,
  onPreview,
  approving,
}: {
  proposal: ScopeProposal;
  onApprove: () => void;
  onPreview: () => void;
  approving: boolean;
}) {
  const aiMeta = typeof proposal.source_distribution_json === 'object' ? proposal.source_distribution_json : null;
  const relevance = proposal.relevance_score ?? aiMeta?.relevance_score;
  const keywords: string[] = proposal.matched_keywords ?? aiMeta?.matched_keywords ?? [];

  return (
    <div className="bg-white border border-hairline rounded-xl p-5 hover:border-ledger/40 hover:shadow-md transition-all duration-200">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <h3 className="font-semibold text-ink truncate">{proposal.suggested_name}</h3>
            {relevance !== undefined && <RelevanceBadge score={relevance} />}
          </div>
          <p className="text-sm text-ink-muted line-clamp-2 mb-3">{proposal.description}</p>

          <div className="flex items-center gap-4 text-xs text-ink-muted mb-3">
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-ledger inline-block" />
              {proposal.estimated_entity_count} entities
            </span>
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-ink-muted/40 inline-block" />
              {proposal.estimated_edge_count} edges
            </span>
            <span>Confidence: {Math.round((proposal.confidence ?? 0) * 100)}%</span>
          </div>

          {keywords.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {keywords.slice(0, 8).map(kw => (
                <KeywordChip key={kw} keyword={kw} />
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2 flex-shrink-0">
          <button
            onClick={onPreview}
            className="flex items-center gap-1.5 text-sm px-3 py-1.5 border border-hairline rounded-lg text-ink hover:border-ledger hover:text-ledger transition-colors"
          >
            <Eye className="w-3.5 h-3.5" />
            Preview
          </button>
          <button
            onClick={onApprove}
            disabled={approving}
            className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-ledger text-white rounded-lg hover:bg-ledger/90 disabled:opacity-50 transition-colors"
          >
            {approving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle className="w-3.5 h-3.5" />}
            Approve
          </button>
        </div>
      </div>
    </div>
  );
}

export function DiscoveryPromptModal({ isOpen, onClose }: Props) {
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState('');
  const [results, setResults] = useState<ScopeProposal[]>([]);
  const [hasSearched, setHasSearched] = useState(false);
  const [previewProposal, setPreviewProposal] = useState<ScopeProposal | null>(null);
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);

  const discoverMutation = useMutation({
    mutationFn: api.scopes.discoverProposals,
    onSuccess: (data) => {
      setResults(data.proposals ?? []);
      setHasSearched(true);
      setDiscoveryError(null);
      queryClient.invalidateQueries({ queryKey: ['proposals'] });
    },
    onError: (err: any) => {
      setDiscoveryError(err?.message || 'Discovery failed. The LLM may be unavailable.');
      setHasSearched(true);
    },
  });

  const handleApprove = async (proposal: ScopeProposal) => {
    setApprovingId(proposal.id);
    try {
      await api.scopes.approveProposal(proposal.id);
      setResults(prev => prev.filter(p => p.id !== proposal.id));
      queryClient.invalidateQueries({ queryKey: ['proposals'] });
      queryClient.invalidateQueries({ queryKey: ['scopes'] });
    } catch (e: any) {
      alert(e?.message || 'Approval failed');
    } finally {
      setApprovingId(null);
    }
  };

  const handleClose = () => {
    setPrompt('');
    setResults([]);
    setHasSearched(false);
    setDiscoveryError(null);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
        <div
          className="bg-white w-full max-w-2xl rounded-2xl shadow-2xl flex flex-col overflow-hidden"
          style={{ maxHeight: '90vh' }}
        >
          {/* Header */}
          <div className="relative bg-gradient-to-r from-ledger/10 via-ledger/5 to-transparent border-b border-hairline p-6">
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-ledger/15 flex items-center justify-center">
                  <Sparkles className="w-5 h-5 text-ledger" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-ink">AI-Guided Community Discovery</h2>
                  <p className="text-sm text-ink-muted">Describe the community you want to find</p>
                </div>
              </div>
              <button
                onClick={handleClose}
                className="p-1.5 rounded-lg text-ink-muted hover:text-ink hover:bg-hairline transition-colors flex-shrink-0"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* Search area */}
          <div className="p-6 border-b border-hairline">
            <textarea
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              placeholder={'e.g. "payment processing services" or "authentication and identity infrastructure"'}
              rows={3}
              maxLength={500}
              className="w-full bg-paper border border-hairline rounded-xl px-4 py-3 text-ink placeholder:text-ink-muted/50 focus:outline-none focus:ring-2 focus:ring-ledger/40 focus:border-ledger resize-none text-sm transition-all"
            />
            <div className="flex items-center justify-between mt-3 gap-3 flex-wrap">
              <div className="flex flex-wrap gap-1.5">
                <span className="text-xs text-ink-muted self-center mr-1">Try:</span>
                {EXAMPLE_PROMPTS.map(ex => (
                  <button
                    key={ex}
                    onClick={() => setPrompt(ex)}
                    className="text-xs px-2.5 py-1 rounded-full bg-paper border border-hairline text-ink-muted hover:border-ledger hover:text-ledger transition-colors"
                  >
                    {ex}
                  </button>
                ))}
              </div>
              <button
                onClick={() => discoverMutation.mutate({ prompt, max_communities: 10, min_entities: 3 })}
                disabled={!prompt.trim() || discoverMutation.isPending}
                className="flex items-center gap-2 bg-ledger hover:bg-ledger/90 disabled:opacity-50 text-white px-5 py-2 rounded-xl text-sm font-medium transition-colors flex-shrink-0"
              >
                {discoverMutation.isPending
                  ? <Loader2 className="w-4 h-4 animate-spin" />
                  : <Search className="w-4 h-4" />}
                {discoverMutation.isPending ? 'Discovering…' : 'Discover'}
              </button>
            </div>
          </div>

          {/* Results */}
          <div className="flex-1 overflow-y-auto">
            {discoverMutation.isPending && (
              <div className="flex flex-col items-center justify-center py-16 gap-4 text-ink-muted">
                <Loader2 className="w-8 h-8 animate-spin text-ledger" />
                <div className="text-center">
                  <p className="font-medium text-ink">Analyzing knowledge graph…</p>
                  <p className="text-sm mt-1">The AI is scoring entities and forming communities</p>
                </div>
              </div>
            )}

            {!discoverMutation.isPending && hasSearched && discoveryError && (
              <div className="p-6">
                <div className="flex items-start gap-3 p-4 bg-alert/8 border border-alert/20 rounded-xl">
                  <AlertCircle className="w-5 h-5 text-alert flex-shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-medium text-ink">Discovery failed</p>
                    <p className="text-sm text-ink-muted mt-1">{discoveryError}</p>
                    <p className="text-sm text-ink-muted mt-2">
                      Try <strong>Structural Discovery</strong> instead — it uses graph analysis without AI.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {!discoverMutation.isPending && hasSearched && !discoveryError && results.length === 0 && (
              <div className="flex flex-col items-center justify-center py-16 text-ink-muted">
                <Sparkles className="w-8 h-8 mb-3 opacity-30" />
                <p className="font-medium text-ink">No matching communities found</p>
                <p className="text-sm mt-1">Try a broader description or different keywords</p>
              </div>
            )}

            {!discoverMutation.isPending && results.length > 0 && (
              <div className="p-6 space-y-3">
                <div className="flex items-center justify-between mb-1">
                  <p className="text-sm font-medium text-ink">
                    {results.length} communit{results.length === 1 ? 'y' : 'ies'} found
                  </p>
                  <p className="text-xs text-ink-muted">Sorted by relevance</p>
                </div>
                {results.map(proposal => (
                  <ProposalCard
                    key={proposal.id}
                    proposal={proposal}
                    onApprove={() => handleApprove(proposal)}
                    onPreview={() => setPreviewProposal(proposal)}
                    approving={approvingId === proposal.id}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      <CommunityPreviewModal
        isOpen={!!previewProposal}
        onClose={() => setPreviewProposal(null)}
        proposal={previewProposal}
      />
    </>
  );
}
