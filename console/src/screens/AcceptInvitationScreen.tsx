import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import AuthLayout from '../components/auth/AuthLayout';
import AuthCard from '../components/auth/AuthCard';

export default function AcceptInvitationScreen() {
  const [passcode, setPasscode] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(true);
  
  useEffect(() => {
    // No longer verifying token on load. User just enters email and passcode.
    setVerifying(false);
  }, []);

  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) return;
    
    setLoading(true);
    setError(null);
    
    try {
      await api.auth.completeSignup({ email, passcode, password });
      navigate('/login');
    } catch (err: any) {
      setError(err.message || 'An error occurred during signup');
    } finally {
      setLoading(false);
    }
  };

  if (verifying) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-paper text-ink font-sans">
        <div className="animate-pulse text-ink-muted">Verifying invitation...</div>
      </div>
    );
  }

  return (
    <AuthLayout title="Welcome to Synapse" subtitle="Set up your account using your email and passcode">
      <AuthCard>
        <form className="space-y-5" onSubmit={handleSubmit}>
          <div>
            <label className="block text-sm font-medium text-ink mb-1.5" htmlFor="email">
              Email Address
            </label>
            <input
              id="email"
              type="email"
              value={email || ''}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-4 py-2 bg-white border border-hairline rounded-md focus:outline-none transition-all placeholder:text-ink-muted/50"
              required
              placeholder="name@company.com"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-ink mb-1.5" htmlFor="passcode">
              One-Time Passcode
            </label>
            <input
              id="passcode"
              type="text"
              value={passcode}
              onChange={(e) => setPasscode(e.target.value.toUpperCase())}
              className="w-full px-4 py-2 bg-white border border-hairline rounded-md focus:outline-none transition-all font-mono tracking-widest uppercase placeholder:text-ink-muted/50"
              required
              placeholder="e.g. 8A3B9F2E"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-ink mb-1.5" htmlFor="password">
              New Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-2 bg-white border border-hairline rounded-md focus:outline-none transition-all placeholder:text-ink-muted/50"
              required
            />
          </div>

          {error && (
            <div className="p-3 rounded-md bg-alert/10 border border-alert/20 text-alert text-sm" role="alert" aria-live="polite">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !!error}
            className="w-full py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-ledger hover:bg-ledger/90 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {loading ? 'Creating Account...' : 'Create Account'}
          </button>
        </form>
      </AuthCard>
    </AuthLayout>
  );
}
