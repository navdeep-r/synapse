import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { Team } from '../../api/client';
import { api } from '../../api/client';
import { Dialog } from '../ui/Dialog';

export function TeamManager() {
  const queryClient = useQueryClient();
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newTeam, setNewTeam] = useState({ name: '', description: '' });
  const [selectedTeam, setSelectedTeam] = useState<Team | null>(null);

  const { data: teams = [], isLoading } = useQuery({
    queryKey: ['teams'],
    queryFn: api.teams.list,
  });

  const { data: teamDetails, isLoading: isLoadingDetails } = useQuery({
    queryKey: ['teams', selectedTeam?.id],
    queryFn: () => selectedTeam ? api.teams.get(selectedTeam.id) : Promise.resolve(null),
    enabled: !!selectedTeam,
  });

  const { data: users = [] } = useQuery({
    queryKey: ['users'],
    queryFn: api.users.list,
  });

  const createTeam = useMutation({
    mutationFn: api.teams.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      setIsCreateOpen(false);
      setNewTeam({ name: '', description: '' });
    }
  });

  const deleteTeam = useMutation({
    mutationFn: api.teams.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      setSelectedTeam(null);
    }
  });

  const addMember = useMutation({
    mutationFn: ({ teamId, userId }: { teamId: string, userId: string }) => api.teams.addMember(teamId, userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['teams', selectedTeam?.id] })
  });

  const removeMember = useMutation({
    mutationFn: ({ teamId, userId }: { teamId: string, userId: string }) => api.teams.removeMember(teamId, userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['teams', selectedTeam?.id] })
  });

  if (isLoading) return <div className="py-12 text-center text-ink-muted">Loading teams...</div>;

  return (
    <div className="flex gap-6 h-[calc(100vh-16rem)]">
      {/* Teams List */}
      <div className="w-1/3 flex flex-col gap-4 overflow-y-auto pr-2">
        <div className="flex justify-between items-center mb-2">
          <h2 className="text-xl font-medium text-ink">Teams</h2>
          <button
            onClick={() => setIsCreateOpen(true)}
            className="text-ledger hover:text-blue-300 text-sm font-medium"
          >
            + New Team
          </button>
        </div>

        {teams.length === 0 ? (
          <div className="py-8 text-center text-ink-muted border border-dashed border-hairline rounded-lg">
            No teams created yet.
          </div>
        ) : (
          teams.map(team => (
            <button
              key={team.id}
              onClick={() => setSelectedTeam(team)}
              className={`text-left p-4 rounded-lg border transition-colors ${
                selectedTeam?.id === team.id
                  ? 'border-ledger bg-ledger/10'
                  : 'border-hairline bg-white hover:border-ledger'
              }`}
            >
              <h3 className="font-medium text-ink mb-1">{team.name}</h3>
              <p className="text-xs text-ink-muted line-clamp-2">{team.description}</p>
            </button>
          ))
        )}
      </div>

      {/* Team Details & Members */}
      <div className="w-2/3 bg-white border border-hairline shadow-sm rounded-lg overflow-hidden flex flex-col">
        {selectedTeam ? (
          <>
            <div className="p-6 border-b border-hairline flex justify-between items-start">
              <div>
                <h2 className="text-2xl font-semibold text-ink mb-2">{selectedTeam.name}</h2>
                <p className="text-ink-muted text-sm">{selectedTeam.description}</p>
              </div>
              <button
                onClick={() => {
                  if (confirm(`Delete team ${selectedTeam.name}?`)) {
                    deleteTeam.mutate(selectedTeam.id);
                  }
                }}
                className="text-alert hover:text-alert/80 text-sm font-medium bg-red-400/10 px-3 py-1.5 rounded-md transition-colors"
              >
                Delete Team
              </button>
            </div>
            
            <div className="p-6 flex-grow overflow-y-auto">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-lg font-medium text-ink">Members</h3>
                <div className="flex items-center gap-2">
                  <select 
                    className="bg-paper border border-hairline text-ink text-sm rounded-md px-3 py-1.5 outline-none"
                    onChange={(e) => {
                      if (e.target.value) {
                        addMember.mutate({ teamId: selectedTeam.id, userId: e.target.value });
                        e.target.value = "";
                      }
                    }}
                    defaultValue=""
                  >
                    <option value="" disabled>Add member...</option>
                    {users
                      .filter(u => !teamDetails?.members?.some(m => m.id === u.id))
                      .map(u => (
                        <option key={u.id} value={u.id}>{u.email}</option>
                      ))}
                  </select>
                </div>
              </div>

              {isLoadingDetails ? (
                <div className="py-8 text-center text-ink-muted">Loading members...</div>
              ) : teamDetails?.members?.length === 0 ? (
                <div className="py-8 text-center text-ink-muted border border-dashed border-hairline rounded-lg">
                  No members in this team.
                </div>
              ) : (
                <div className="border border-hairline rounded-lg overflow-hidden">
                  <table className="w-full text-left text-sm text-ink">
                    <thead className="bg-paper border-b border-hairline">
                      <tr>
                        <th className="px-4 py-3 font-medium">User</th>
                        <th className="px-4 py-3 font-medium">Joined</th>
                        <th className="px-4 py-3 font-medium text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-hairline">
                      {teamDetails?.members?.map(member => (
                        <tr key={member.id} className="hover:bg-hairline/50">
                          <td className="px-4 py-3">
                            <div className="font-medium text-ink">{member.display_name || 'User'}</div>
                            <div className="text-xs text-ink-muted">{member.email}</div>
                          </td>
                          <td className="px-4 py-3">{new Date(member.joined_at).toLocaleDateString()}</td>
                          <td className="px-4 py-3 text-right">
                            <button
                              onClick={() => removeMember.mutate({ teamId: selectedTeam.id, userId: member.id })}
                              className="text-alert hover:text-alert/80 text-xs font-medium"
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex items-center justify-center h-full text-ink-muted p-12 text-center">
            Select a team to view its members and details.
          </div>
        )}
      </div>

      <Dialog
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        title="Create Team"
        description="Create a new team to group users."
        footer={
          <>
            <button onClick={() => setIsCreateOpen(false)} className="px-4 py-2 text-sm font-medium text-ink-muted hover:text-ink">Cancel</button>
            <button
              onClick={() => createTeam.mutate(newTeam)}
              disabled={createTeam.isPending || !newTeam.name}
              className="bg-ledger hover:bg-ledger/90 text-white px-4 py-2 rounded-md text-sm font-medium disabled:opacity-50"
            >
              {createTeam.isPending ? "Creating..." : "Create"}
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Team Name</label>
            <input
              type="text"
              value={newTeam.name}
              onChange={e => setNewTeam({ ...newTeam, name: e.target.value })}
              className="w-full bg-paper border border-hairline rounded-md px-3 py-2 text-ink focus:outline-none focus:ring-1 focus:ring-ledger"
              placeholder="e.g. Frontend Engineers"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Description</label>
            <textarea
              value={newTeam.description}
              onChange={e => setNewTeam({ ...newTeam, description: e.target.value })}
              className="w-full bg-paper border border-hairline rounded-md px-3 py-2 text-ink focus:outline-none focus:ring-1 focus:ring-ledger h-24 resize-none"
              placeholder="What is this team responsible for?"
            />
          </div>
        </div>
      </Dialog>
    </div>
  );
}
