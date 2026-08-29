import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Badge } from '../ui/Badge';
import { Dialog } from '../ui/Dialog';

export function RoleManager() {
  const queryClient = useQueryClient();
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newRole, setNewRole] = useState({ name: '', description: '', clearance: 'internal' });
  const [createError, setCreateError] = useState<string | null>(null);

  const { data: roles = [], isLoading } = useQuery({
    queryKey: ['roles'],
    queryFn: api.roles.list,
  });

  const createRole = useMutation({
    mutationFn: api.roles.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] });
      setIsCreateOpen(false);
      setNewRole({ name: '', description: '', clearance: 'internal' });
      setCreateError(null);
    },
    onError: (err: any) => {
      setCreateError(err?.message || 'Failed to create role');
    }
  });

  const deleteRole = useMutation({
    mutationFn: api.roles.delete,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['roles'] })
  });

  const clearances = ["public", "internal", "confidential", "restricted"];

  if (isLoading) return <div className="py-12 text-center text-ink-muted">Loading roles...</div>;

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-xl font-medium text-ink">Roles</h2>
        <button
          onClick={() => setIsCreateOpen(true)}
          className="bg-ledger hover:bg-ledger/90 text-white px-4 py-2 rounded-md font-medium text-sm transition-colors"
        >
          Create Role
        </button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {roles.map(role => (
          <div key={role.id} className="bg-white border border-hairline shadow-sm rounded-lg p-5 flex flex-col h-full">
            <div className="flex justify-between items-start mb-2">
              <h3 className="font-medium text-ink">{role.name}</h3>
              {role.is_system_role && <Badge status="neutral">System</Badge>}
            </div>
            <p className="text-sm text-ink-muted flex-grow mb-4">{role.description}</p>
            <div className="flex items-center justify-between mt-auto">
              <span className="text-xs uppercase tracking-wider font-semibold text-ink-muted bg-hairline px-2 py-1 rounded">
                {role.clearance}
              </span>
              {!role.is_system_role && (
                <button
                  onClick={() => {
                    if (confirm(`Delete role ${role.name}?`)) {
                      deleteRole.mutate(role.id);
                    }
                  }}
                  className="text-alert hover:text-alert/80 text-sm font-medium"
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      <Dialog
        isOpen={isCreateOpen}
        onClose={() => { setIsCreateOpen(false); setCreateError(null); }}
        title="Create Role"
        description="Define a new role and its clearance level."
        footer={
          <>
            <button
              onClick={() => setIsCreateOpen(false)}
              className="px-4 py-2 text-sm font-medium text-ink-muted hover:text-ink"
            >
              Cancel
            </button>
            <button
              onClick={() => createRole.mutate(newRole)}
              disabled={createRole.isPending || !newRole.name}
              className="bg-ledger hover:bg-ledger/90 text-white px-4 py-2 rounded-md text-sm font-medium disabled:opacity-50"
            >
              {createRole.isPending ? "Creating..." : "Create"}
            </button>
          </>
        }
      >
        <div className="space-y-4">
          {createError && (
            <div className="p-3 rounded-md bg-alert/10 border border-alert/20 text-alert text-sm">
              {createError}
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Role Name</label>
            <input
              type="text"
              value={newRole.name}
              onChange={e => setNewRole({ ...newRole, name: e.target.value })}
              className="w-full bg-paper border border-hairline rounded-md px-3 py-2 text-ink focus:outline-none focus:ring-1 focus:ring-ledger"
              placeholder="e.g. Data Scientist"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Description</label>
            <textarea
              value={newRole.description}
              onChange={e => setNewRole({ ...newRole, description: e.target.value })}
              className="w-full bg-paper border border-hairline rounded-md px-3 py-2 text-ink focus:outline-none focus:ring-1 focus:ring-ledger h-24 resize-none"
              placeholder="What does this role do?"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Clearance Level</label>
            <select
              value={newRole.clearance}
              onChange={e => setNewRole({ ...newRole, clearance: e.target.value })}
              className="w-full bg-paper border border-hairline rounded-md px-3 py-2 text-ink focus:outline-none focus:ring-1 focus:ring-ledger"
            >
              {clearances.map(c => (
                <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
              ))}
            </select>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
