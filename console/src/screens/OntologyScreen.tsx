import * as React from 'react';
import { Network, History, FileEdit } from 'lucide-react';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';

const FIXED_ENTITY_TYPES = [
  { id: '1', name: 'Organization', description: 'Represents a company, institution, or established group.' },
  { id: '2', name: 'Person', description: 'An individual human being.' },
  { id: '3', name: 'Location', description: 'A physical place, region, or address.' },
  { id: '4', name: 'Event', description: 'A significant occurrence with temporal boundaries.' },
  { id: '5', name: 'Product', description: 'A tangible or intangible item offered for sale or use.' },
  { id: '6', name: 'Concept', description: 'An abstract idea or domain of knowledge.' },
  { id: '7', name: 'Document', description: 'A written or recorded piece of information.' },
];

export interface RelationDefinition {
  id: string;
  type: string;
  version: string;
  sources: string;
  targets: string;
  maxActive: number;
  temporal: boolean;
  overlapAllowed: boolean;
}

export interface MigrationRecord {
  id: string;
  date: string;
  from: string;
  to: string;
  user: string;
  status: 'completed' | 'failed' | 'pending';
}

export interface PendingProposal {
  id: string;
  title: string;
  requestedBy: string;
  impact: 'High' | 'Medium' | 'Low';
  currentVal: string;
  proposedVal: string;
  impactReport: string;
  sampleAffectedEdges: string[];
}

export default function OntologyScreen() {
  const [activeTab, setActiveTab] = React.useState<'entities' | 'relations' | 'migrations' | 'propose'>('entities');
  const [editingEntity, setEditingEntity] = React.useState<string | null>(null);
  const [entityTypes, setEntityTypes] = React.useState(FIXED_ENTITY_TYPES);
  const [relations, setRelations] = React.useState<RelationDefinition[]>([]);
  const [migrations, setMigrations] = React.useState<MigrationRecord[]>([]);
  const [proposals, setProposals] = React.useState<PendingProposal[]>([]);

  // Form state
  const [proposalField, setProposalField] = React.useState('max_active_outgoing');
  const [proposalValue, setProposalValue] = React.useState('');
  const [proposalJustification, setProposalJustification] = React.useState('');

  const fetchData = async () => {
    try {
      const entRes = await fetch('/api/v1/ontology/entity-types');
      if (entRes.ok) setEntityTypes(await entRes.json());
      
      const relRes = await fetch('/api/v1/ontology/relation-types');
      if (relRes.ok) {
        const relData = await relRes.json();
        const mappedRels = relData.map((r: any) => ({
          id: r.relation_type,
          type: r.relation_type,
          version: `v${r.version}`,
          sources: (r.source_types || []).join(', '),
          targets: (r.target_types || []).join(', '),
          maxActive: r.max_active_outgoing,
          temporal: r.temporal,
          overlapAllowed: r.overlap_allowed
        }));
        setRelations(mappedRels);
      }

      const migRes = await fetch('/api/v1/ontology/migrations');
      if (migRes.ok) {
        const migData = await migRes.json();
        
        const mappedProposals = migData.filter((m: any) => m.status === 'pending').map((m: any) => ({
          id: m.id,
          title: `Change ${m.field_to_change}`,
          requestedBy: m.proposed_by,
          impact: 'Medium',
          currentVal: 'Unknown', // we don't have this in the schema easily
          proposedVal: String(m.new_value),
          impactReport: m.impact_report,
          sampleAffectedEdges: []
        }));
        setProposals(mappedProposals);

        const mappedMigrations = migData.filter((m: any) => m.status !== 'pending').map((m: any) => ({
          id: m.id,
          date: 'Just now',
          from: m.field_to_change,
          to: String(m.new_value),
          user: m.proposed_by,
          status: m.status === 'approved' ? 'completed' : m.status
        }));
        setMigrations(mappedMigrations);
      }
    } catch (e) {
      console.error(e);
    }
  };

  React.useEffect(() => {
    fetchData();
  }, []);

  const handlePropose = async () => {
    let parsedVal: any = proposalValue;
    if (proposalField === 'max_active_outgoing') {
      parsedVal = parseInt(proposalValue, 10);
    } else if (proposalField === 'temporal' || proposalField === 'overlap_allowed') {
      parsedVal = proposalValue.toLowerCase() === 'true';
    }

    try {
      const res = await fetch('/api/v1/ontology/changes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          relation_type: 'FOUNDED_BY', // defaults to only one we have
          field: proposalField,
          old_value: null,
          new_value: parsedVal,
          justification: proposalJustification
        })
      });
      if (res.ok) {
        alert('Proposal submitted successfully!');
        setProposalValue('');
        setProposalJustification('');
        fetchData();
      } else {
        alert('Failed to submit proposal');
      }
    } catch (e) {
      console.error(e);
      alert('Error submitting proposal');
    }
  };

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-display text-ink tracking-tight">Ontology</h1>
        <Button variant="primary" onClick={() => setActiveTab('propose')}>
          <Network className="mr-2 h-4 w-4" /> Propose Change
        </Button>
      </div>

      <div className="flex border-b border-hairline">
        {(['entities', 'relations', 'migrations', 'propose'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-3 font-medium text-body capitalize border-b-2 transition-colors ${
              activeTab === tab 
                ? 'border-ledger text-ledger' 
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {tab === 'propose' ? 'Proposals & Approvals' : tab}
          </button>
        ))}
      </div>

      {activeTab === 'entities' && (
        <div className="space-y-4">
          <h2 className="text-h2 text-ink">Entity Type Registry</h2>
          <div className="grid grid-cols-1 gap-4">
            {entityTypes.map(entity => (
              <div key={entity.id} className="p-4 border border-hairline bg-white rounded-lg flex items-start gap-4">
                <div className="w-1/4 font-medium text-ink text-body">{entity.name}</div>
                <div className="flex-1">
                  {editingEntity === entity.id ? (
                    <div className="flex items-center gap-2">
                      <input 
                        type="text" 
                        defaultValue={entity.description}
                        className="flex-1 border border-ledger rounded-md px-3 py-1.5 text-body focus:outline-none"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            const val = (e.target as HTMLInputElement).value;
                            setEntityTypes(list => list.map(item => item.id === entity.id ? { ...item, description: val } : item));
                            setEditingEntity(null);
                          }
                        }}
                      />
                      <Button variant="ghost" size="sm" onClick={() => setEditingEntity(null)}>Save</Button>
                    </div>
                  ) : (
                    <div className="text-ink-muted text-body pr-8 flex items-center justify-between group">
                      <span>{entity.description}</span>
                      <button onClick={() => setEditingEntity(entity.id)} className="opacity-0 group-hover:opacity-100 transition-opacity p-1 text-ledger">
                        <FileEdit className="h-4 w-4" />
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'relations' && (
        <div className="space-y-4">
          <h2 className="text-h2 text-ink">Relation Type Registry</h2>
          <div className="overflow-hidden rounded-lg border border-hairline bg-white">
            <table className="w-full text-left text-body">
              <thead className="bg-paper border-b border-hairline text-ink-muted text-caption uppercase tracking-wider">
                <tr>
                  <th className="px-4 py-3 font-medium">Relation Type</th>
                  <th className="px-4 py-3 font-medium">Version</th>
                  <th className="px-4 py-3 font-medium">Sources → Targets</th>
                  <th className="px-4 py-3 font-medium">Rules</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {relations.map(rel => (
                  <tr key={rel.id} className="hover:bg-paper/50 transition-colors cursor-pointer">
                    <td className="px-4 py-3 font-medium text-ink">{rel.type}</td>
                    <td className="px-4 py-3 font-mono text-ink-muted">{rel.version}</td>
                    <td className="px-4 py-3 text-ink-muted">{rel.sources} → {rel.targets}</td>
                    <td className="px-4 py-3 text-caption text-ink-muted">
                      Max out: {rel.maxActive} | Temporal: {rel.temporal ? 'Yes' : 'No'} | Overlap: {rel.overlapAllowed ? 'Yes' : 'No'}
                    </td>
                  </tr>
                ))}
                {relations.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-ink-muted italic">
                      No relation types registered in ontology.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === 'migrations' && (
        <div className="space-y-4">
          <h2 className="text-h2 text-ink flex items-center"><History className="mr-2 h-5 w-5" /> Migration History</h2>
          <div className="space-y-4">
            {migrations.map(migration => (
              <div key={migration.id} className="p-4 border border-hairline bg-white rounded-lg flex items-center justify-between">
                <div>
                  <div className="font-medium text-ink">Migrated from {migration.from} to {migration.to}</div>
                  <div className="text-caption text-ink-muted">Requested by {migration.user}</div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="font-mono text-caption text-ink-muted">{migration.date}</div>
                  <Badge status="confirmed">{migration.status}</Badge>
                </div>
              </div>
            ))}
            {migrations.length === 0 && (
              <div className="p-8 text-center text-ink-muted border border-hairline bg-white rounded-lg italic">
                No migration history recorded.
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'propose' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          <div className="space-y-4">
            <h2 className="text-h2 text-ink">Propose Change</h2>
            <div className="p-6 border border-hairline rounded-lg bg-white space-y-4">
              <div>
                <label className="block text-caption text-ink-muted font-medium mb-1 uppercase">Field to change</label>
                <select 
                  value={proposalField}
                  onChange={e => setProposalField(e.target.value)}
                  className="w-full border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger"
                >
                  <option value="max_active_outgoing">Relation: max_active_outgoing</option>
                  <option value="temporal">Relation: temporal</option>
                  <option value="overlap_allowed">Relation: overlap_allowed</option>
                </select>
              </div>
              <div>
                <label className="block text-caption text-ink-muted font-medium mb-1 uppercase">New Value</label>
                <input 
                  type="text" 
                  value={proposalValue}
                  onChange={e => setProposalValue(e.target.value)}
                  className="w-full border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger" 
                  placeholder="e.g. 5 or true/false" 
                />
              </div>
              <div>
                <label className="block text-caption text-ink-muted font-medium uppercase mb-1">Justification (Required)</label>
                <textarea 
                  value={proposalJustification}
                  onChange={e => setProposalJustification(e.target.value)}
                  className="w-full border border-hairline rounded-md px-3 py-2 text-body focus:outline-none focus:border-ledger min-h-[100px]" 
                  placeholder="Explain why this structural change is necessary..."
                />
              </div>
              <Button variant="primary" className="w-full" onClick={handlePropose} disabled={!proposalValue || !proposalJustification}>Submit Proposal</Button>
            </div>
          </div>

          <div className="space-y-4">
            <h2 className="text-h2 text-ink">Pending Approvals</h2>
            {proposals.length === 0 ? (
              <div className="p-8 text-center text-ink-muted border border-hairline bg-white rounded-lg italic">
                No proposals pending review.
              </div>
            ) : (
              proposals.map(proposal => (
                <div key={proposal.id} className="p-6 border border-hairline rounded-lg bg-white space-y-4">
                  <div className="flex justify-between items-start mb-2">
                    <div>
                      <div className="font-medium text-ink">{proposal.title}</div>
                      <div className="text-caption text-ink-muted">Requested by {proposal.requestedBy} • Impact: {proposal.impact}</div>
                    </div>
                    <Badge status="signal">Pending</Badge>
                  </div>
                  <div className="bg-paper p-3 rounded text-body font-mono text-ink-muted text-sm border border-hairline">
                    Current: {proposal.currentVal}<br/>
                    Proposed: {proposal.proposedVal}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
