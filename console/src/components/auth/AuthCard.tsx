import React from 'react';

interface AuthCardProps {
  children: React.ReactNode;
}

export default function AuthCard({ children }: AuthCardProps) {
  return (
    <div className="bg-white border border-slate-200 rounded-2xl shadow-xl overflow-hidden relative">
      <div className="p-8 sm:p-10 relative z-10">
        {children}
      </div>
    </div>
  );
}
