import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Badge } from '../ui/Badge';
import { Dialog } from '../ui/Dialog';
import { MultiSelect } from '../ui/MultiSelect';

export function MemberManager() {
  const queryClient = useQueryClient();
  const [isInviteOpen, setIsInviteOpen] = useState(false);
  const [inviteData, setInviteData] = useState({ email: '', role_ids: [] as string[], team_ids: [] as string[] });
  const [generatedPasscode, setGeneratedPasscode] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);

  const { data: users = [], isLoading: loadingUsers } = useQuery({
    queryKey: ['users'],
    queryFn: api.users.list,
  });

  const { data: roles = [] } = useQuery({
    queryKey: ['roles'],
    queryFn: api.roles.list,
  });

  const { data: teams = [] } = useQuery({
    queryKey: ['teams'],
    queryFn: api.teams.list,
  });

  const inviteUser = useMutation({
    mutationFn: api.users.invite,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setGeneratedPasscode(data.passcode);
      setInviteData({ email: '', role_ids: [], team_ids: [] });
      setInviteError(null);
    },
    onError: (err: any) => {
      setInviteError(err?.message || 'Failed to send invitation');
    }
  });

  if (loadingUsers) return <div className="py-12 text-center text-ink-muted">Loading members...</div>;

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-xl font-medium text-ink">Organization Members</h2>
        <button
          className="bg-ledger hover:bg-ledger/90 text-white px-4 py-2 rounded-md font-medium text-sm transition-colors"
          onClick={() => {
            setGeneratedPasscode(null);
            setInviteError(null);
            setIsInviteOpen(true);
          }}
        >
          Invite Member
        </button>
      </div>

      <div className="bg-white border border-hairline shadow-sm rounded-lg overflow-hidden">
        <table className="w-full text-left text-sm text-ink">
          <thead className="bg-paper border-b border-hairline">
            <tr>
              <th className="px-6 py-3 font-medium">Email</th>
              <th className="px-6 py-3 font-medium">Status</th>
              <th className="px-6 py-3 font-medium">Joined</th>
              <th className="px-6 py-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {users.map((u: any) => (
              <tr key={u.id} className="hover:bg-hairline/50">
                <td className="px-6 py-4">{u.email}</td>
                <td className="px-6 py-4">
                  <Badge status={u.status === 'active' ? 'confirmed' : 'neutral'}>{u.status}</Badge>
                </td>
                <td className="px-6 py-4">{u.created_at ? new Date(u.created_at).toLocaleDateString() : '-'}</td>
                <td className="px-6 py-4 text-right space-x-3">
                  <button className="text-ledger hover:text-blue-300 text-sm font-medium" onClick={() => {
                    if (confirm("Revoke all sessions for this user?")) {
                      api.users.revokeSessions(u.id).catch(e => alert(e.message));
                    }
                  }}>
                    Revoke Sessions
                  </button>
                  {u.status === 'active' && (
                    <button className="text-alert hover:text-red-400 text-sm font-medium transition-colors" onClick={() => {
                      if (confirm("Suspend this member? This will deactivate them and revoke all their sessions.")) {
                        api.users.update(u.id, { is_active: 0, status: 'suspended' })
                          .then(() => queryClient.invalidateQueries({ queryKey: ['users'] }))
                          .catch(e => alert(e.message));
                      }
                    }}>
                      Suspend
                    </button>
                  )}
                  {u.status === 'suspended' && (
                    <button className="text-confirmed hover:text-green-400 text-sm font-medium transition-colors" onClick={() => {
                      if (confirm("Re-activate this suspended member?")) {
                        api.users.update(u.id, { is_active: 1, status: 'active' })
                          .then(() => queryClient.invalidateQueries({ queryKey: ['users'] }))
                          .catch(e => alert(e.message));
                      }
                    }}>
                      Re-activate
                    </button>
                  )}
                  {u.status === 'pending_invitation' && (
                    <>
                      <button className="text-confirmed hover:text-green-400 text-sm font-medium transition-colors" onClick={() => {
                        if (confirm("Manually activate this member? They will need to use 'Forgot Password' to set their initial password.")) {
                          api.users.update(u.id, { is_active: 1, status: 'active' })
                            .then(() => queryClient.invalidateQueries({ queryKey: ['users'] }))
                            .catch(e => alert(e.message));
                        }
                      }}>
                        Activate
                      </button>
                      <span className="text-ink-muted text-sm italic px-2 border-l border-hairline ml-2">Pending setup</span>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {isInviteOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-ink/40 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden border border-hairline flex flex-col animate-in zoom-in-95 duration-200">
            {/* Header with gradient */}
            <div className={`p-6 pb-4 bg-gradient-to-br ${generatedPasscode ? 'from-green-50 to-emerald-50' : 'from-slate-50 to-indigo-50/30'} border-b border-hairline`}>
              <h3 className="text-xl font-semibold text-ink">
                {generatedPasscode ? "✨ Invitation Sent!" : "Invite New Member"}
              </h3>
              <p className="text-sm text-ink-muted mt-1.5 leading-relaxed">
                {generatedPasscode 
                  ? "The user has been invited successfully. Please share this one-time passcode with them securely." 
                  : "Add a new member to your organization and configure their access levels."}
              </p>
            </div>
            
            {/* Body */}
            <div className="p-6 space-y-5 bg-white">
              {inviteError && (
                <div className="p-3 rounded-lg bg-red-50 border border-red-100 text-red-600 text-sm font-medium flex items-center">
                  <span className="mr-2">⚠️</span> {inviteError}
                </div>
              )}
              
              {generatedPasscode ? (
                <div className="bg-gradient-to-b from-green-50/50 to-transparent border border-green-200 p-5 rounded-xl text-center">
                  <p className="text-sm font-medium text-green-800 mb-3 uppercase tracking-wider text-xs">One-Time Passcode</p>
                  <div className="bg-white border shadow-inner p-4 rounded-lg text-3xl font-mono text-ink tracking-[0.2em] font-bold select-all">
                    {generatedPasscode}
                  </div>
                  <p className="text-xs mt-4 text-green-700/80">This passcode is required for the user's first login. It will not be shown again.</p>
                </div>
              ) : (
                <div className="space-y-5">
                  <div className="space-y-1.5">
                    <label className="block text-sm font-medium text-ink">Email Address</label>
                    <input
                      type="email"
                      value={inviteData.email}
                      onChange={e => setInviteData({ ...inviteData, email: e.target.value })}
                      className="w-full bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5 text-ink focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 transition-all shadow-sm"
                      placeholder="name@example.com"
                      autoFocus
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="block text-sm font-medium text-ink">Assign Roles <span className="text-ink-muted font-normal">(Optional)</span></label>
                    <div className="shadow-sm rounded-lg">
                      <MultiSelect
                        options={roles.map(r => ({ value: r.id, label: r.name, description: r.clearance }))}
                        value={inviteData.role_ids}
                        onChange={val => setInviteData({ ...inviteData, role_ids: val })}
                        placeholder="Select roles..."
                      />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <label className="block text-sm font-medium text-ink">Assign to Teams <span className="text-ink-muted font-normal">(Optional)</span></label>
                    <div className="shadow-sm rounded-lg">
                      <MultiSelect
                        options={teams.map(t => ({ value: t.id, label: t.name }))}
                        value={inviteData.team_ids}
                        onChange={val => setInviteData({ ...inviteData, team_ids: val })}
                        placeholder="Select teams..."
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="p-4 px-6 bg-slate-50 border-t border-hairline flex justify-end gap-3 rounded-b-2xl">
              {generatedPasscode ? (
                <button
                  onClick={() => setIsInviteOpen(false)}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white px-5 py-2.5 rounded-lg text-sm font-medium transition-colors shadow-sm w-full"
                >
                  Done
                </button>
              ) : (
                <>
                  <button 
                    onClick={() => setIsInviteOpen(false)} 
                    className="px-4 py-2.5 text-sm font-medium text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-lg transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => inviteUser.mutate(inviteData)}
                    disabled={inviteUser.isPending || !inviteData.email}
                    className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2.5 rounded-lg text-sm font-medium transition-all shadow-sm disabled:opacity-50 disabled:pointer-events-none flex items-center"
                  >
                    {inviteUser.isPending ? (
                      <><span className="w-4 h-4 mr-2 border-2 border-white/30 border-t-white rounded-full animate-spin"></span> Sending...</>
                    ) : "Send Invite"}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
