import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AuthLayout from '../components/auth/AuthLayout';
import AuthCard from '../components/auth/AuthCard';

export default function LoginScreen() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const res = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, password }),
      });

      if (!res.ok) {
        let msg = 'Login failed';
        try {
          const data = await res.json();
          if (typeof data.detail === 'string') {
            msg = data.detail;
          } else if (data.detail) {
            msg = JSON.stringify(data.detail);
          }
        } catch (_) {}
        throw new Error(msg);
      }

      const data = await res.json();
      localStorage.setItem('synapse_access_token', data.access_token);
      navigate('/');
    } catch (err: any) {
      setError(err.message || 'An error occurred during login');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout title="Synapse Enterprise" subtitle="Sign in to your account">
      <AuthCard>
        <form className="space-y-5" onSubmit={handleLogin}>
          <div>
            <label className="block text-sm font-medium text-ink mb-1.5" htmlFor="email">
              Email Address
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-4 py-3 bg-white border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-ledger/50 focus:border-ledger transition-all placeholder:text-slate-400 text-ink"
              placeholder="admin@synapse.local"
              required
            />
          </div>
          
          <div>
            <div className="flex justify-between items-center mb-1.5">
              <label className="block text-sm font-medium text-ink" htmlFor="password">
                Password
              </label>
              <a href="/forgot-password" className="text-sm text-ledger hover:text-ledger/80 transition-colors">
                Forgot password?
              </a>
            </div>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-3 bg-white border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-ledger/50 focus:border-ledger transition-all placeholder:text-slate-400 text-ink"
              placeholder="••••••••"
              required
            />
          </div>

          {error && (
            <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm backdrop-blur-sm" role="alert" aria-live="polite">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 px-4 rounded-xl shadow-lg shadow-ledger/20 text-sm font-semibold text-white bg-ledger hover:bg-ledger/90 focus:outline-none focus:ring-2 focus:ring-ledger focus:ring-offset-2 focus:ring-offset-white disabled:opacity-50 disabled:cursor-not-allowed transition-all active:scale-[0.98]"
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>
        
        <div className="mt-6 pt-6 border-t border-hairline text-center">
          <p className="text-sm text-ink-muted">
            Have an invitation?{' '}
            <a href="/accept-invitation" className="font-medium text-ledger hover:text-ledger/80 transition-colors">
              Set up your account
            </a>
          </p>
        </div>
      </AuthCard>
    </AuthLayout>
  );
}
