import * as React from 'react';

interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  status: 'signal' | 'alert' | 'confirmed' | 'neutral';
}

export const Badge = React.forwardRef<HTMLDivElement, BadgeProps>(
  ({ className = '', status, children, ...props }, ref) => {
    
    // Status text matches AA contrast requirements at caption size.
    // The background color needs to be subtle (10-20% opacity of the actual token) 
    // or text must be white if background is solid.
    // Given AA contrast rule, let's use solid colored text on a very light background.
    const statuses = {
      signal: 'bg-[#B8863E]/10 text-signal border border-signal/20',
      alert: 'bg-[#A23B2E]/10 text-alert border border-alert/20',
      confirmed: 'bg-[#3F6B4F]/10 text-confirmed border border-confirmed/20',
      neutral: 'bg-hairline text-ink-muted',
    };

    return (
      <div
        ref={ref}
        className={`inline-flex items-center rounded-full px-2 py-0.5 text-caption font-medium ${statuses[status]} ${className}`}
        {...props}
      >
        {children}
      </div>
    );
  }
);
Badge.displayName = 'Badge';
