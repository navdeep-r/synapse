import React, { useState } from 'react';
import { api } from '../api/client';
import AuthLayout from '../components/auth/AuthLayout';
import AuthCard from '../components/auth/AuthCard';

export default function ForgotPasswordScreen() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setMessage(null);
    
    try {
      await api.auth.forgotPassword({ email });
      setMessage('If an account matches that email, a password reset link has been sent (check logs).');
    } catch (err: any) {
      setError(err.message || 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout title="Reset Password" subtitle="Enter your email to request a reset link">
      <AuthCard>
        <form className="space-y-5" onSubmit={handleSubmit}>
          <div>
            <label className="block text-sm font-medium text-ink mb-1.5" htmlFor="email">
              Email Address
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-4 py-2 bg-white border border-hairline rounded-md focus:outline-none transition-all placeholder:text-ink-muted/50"
              required
            />
          </div>

          {error && (
            <div className="p-3 rounded-md bg-alert/10 border border-alert/20 text-alert text-sm" role="alert" aria-live="polite">
              {error}
            </div>
          )}
          
          {message && (
            <div className="p-3 rounded-md bg-confirmed/10 border border-confirmed/20 text-confirmed text-sm" role="status" aria-live="polite">
              {message}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !!message}
            className="w-full py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-ledger hover:bg-ledger/90 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {loading ? 'Sending...' : 'Send Reset Link'}
          </button>
          
          <div className="text-center pt-2">
             <a href="/login" className="text-sm text-ledger hover:text-ledger/80 transition-colors">
               Back to Login
             </a>
          </div>
        </form>
      </AuthCard>
    </AuthLayout>
  );
}
