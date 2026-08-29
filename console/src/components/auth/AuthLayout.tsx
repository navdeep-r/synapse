import React from 'react';
import { Layers } from 'lucide-react';

interface AuthLayoutProps {
  children: React.ReactNode;
  title: string;
  subtitle?: string;
}

export default function AuthLayout({ children, title, subtitle }: AuthLayoutProps) {
  return (
    <div className="min-h-screen bg-white text-ink font-sans flex flex-col justify-center items-center px-4 relative overflow-hidden">
      
      <div className="w-full max-w-md z-10 animate-in fade-in slide-in-from-bottom-8 duration-700 ease-out">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center p-4 bg-white rounded-2xl shadow-sm border border-slate-200 mb-6 transition-transform hover:scale-105 duration-300">
            <Layers className="w-10 h-10 text-ledger" />
          </div>
          <h1 className="text-4xl font-extrabold tracking-tight mb-3 text-ink">{title}</h1>
          {subtitle && <p className="text-ink-muted text-base">{subtitle}</p>}
        </div>
        
        {children}
      </div>
    </div>
  );
}
