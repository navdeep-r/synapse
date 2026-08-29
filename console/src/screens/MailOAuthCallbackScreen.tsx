import React, { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';

export default function MailOAuthCallbackScreen() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading');
  const [errorMsg, setErrorMsg] = useState('');

  useEffect(() => {
    const code = searchParams.get('code');
    if (!code) {
      setStatus('error');
      setErrorMsg('No authorization code returned from Google.');
      return;
    }

    api.mail.oauth.gmailCallback(code)
      .then(() => {
        setStatus('success');
        // Give the user a moment to read the success message
        setTimeout(() => navigate('/mail'), 2000);
      })
      .catch((err) => {
        setStatus('error');
        setErrorMsg(err.detail || err.message || 'Failed to exchange token');
      });
  }, [searchParams, navigate]);

  return (
    <div className="flex h-screen items-center justify-center bg-paper">
      <div className="w-full max-w-md rounded border border-hairline bg-white p-8 shadow-sm text-center">
        {status === 'loading' && (
          <>
            <Loader2 className="mx-auto h-12 w-12 animate-spin text-ink-muted mb-4" />
            <h2 className="text-h3 mb-2 text-ink">Connecting to Gmail...</h2>
            <p className="text-body text-ink-muted">Please wait while we secure your account connection.</p>
          </>
        )}
        
        {status === 'success' && (
          <>
            <CheckCircle2 className="mx-auto h-12 w-12 text-[#0d9488] mb-4" />
            <h2 className="text-h3 mb-2 text-ink">Successfully Connected</h2>
            <p className="text-body text-ink-muted">Your Gmail account has been connected. Initial sync is starting in the background. Redirecting...</p>
          </>
        )}
        
        {status === 'error' && (
          <>
            <AlertCircle className="mx-auto h-12 w-12 text-[#e11d48] mb-4" />
            <h2 className="text-h3 mb-2 text-ink">Connection Failed</h2>
            <p className="text-body text-[#e11d48] mb-6">{errorMsg}</p>
            <button 
              onClick={() => navigate('/mail')}
              className="px-4 py-2 bg-paper border border-hairline rounded hover:bg-black/5 transition-colors"
            >
              Return to Mail Settings
            </button>
          </>
        )}
      </div>
    </div>
  );
}
