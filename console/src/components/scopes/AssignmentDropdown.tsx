import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';

export type AssigneeKind = 'role' | 'team' | 'user';

export interface AssignmentDropdownProps {
  onAssign?: (assigneeKind: AssigneeKind, assigneeId: string) => void;
  disabled?: boolean;
}

export function AssignmentDropdown({ onAssign, disabled = false }: AssignmentDropdownProps) {
  const [assigneeKind, setAssigneeKind] = useState<AssigneeKind>('team');
  const [assigneeId, setAssigneeId] = useState<string>('');

  const { data: roles = [] } = useQuery({ queryKey: ['roles'], queryFn: api.roles.list });
  const { data: teams = [] } = useQuery({ queryKey: ['teams'], queryFn: api.teams.list });
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: api.users.list });

  const handleKindChange = (kind: AssigneeKind) => {
    setAssigneeKind(kind);
    setAssigneeId('');
    if (onAssign) onAssign(kind, '');
  };

  const handleIdChange = (id: string) => {
    setAssigneeId(id);
    if (onAssign) onAssign(assigneeKind, id);
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-ink mb-1">Assignee Type</label>
        <div className="flex gap-2">
          {(['team', 'role', 'user'] as const).map(kind => (
            <button
              key={kind}
              onClick={() => handleKindChange(kind)}
              disabled={disabled}
              className={`flex-1 py-1.5 text-sm font-medium rounded-md border transition-colors ${
                assigneeKind === kind 
                  ? 'bg-ledger/10 border-ledger text-ledger' 
                  : 'bg-white border-hairline text-ink-muted hover:bg-hairline/30'
              } disabled:opacity-50`}
            >
              {kind.charAt(0).toUpperCase() + kind.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-ink mb-1">Select Assignee</label>
        <select
          value={assigneeId}
          onChange={e => handleIdChange(e.target.value)}
          disabled={disabled}
          className="w-full bg-white border border-hairline rounded-md px-3 py-2 text-ink focus:outline-none focus:ring-1 focus:ring-ledger disabled:opacity-50"
        >
          <option value="" disabled>Select {assigneeKind}...</option>
          {assigneeKind === 'team' && teams.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
          {assigneeKind === 'role' && roles.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
          {assigneeKind === 'user' && users.map(u => <option key={u.id} value={u.id}>{u.email}</option>)}
        </select>
      </div>
    </div>
  );
}
