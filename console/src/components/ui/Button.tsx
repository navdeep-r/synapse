import * as React from 'react';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  size?: 'sm' | 'md' | 'lg';
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = '', variant = 'primary', size = 'md', ...props }, ref) => {
    
    const baseStyles = 'inline-flex items-center justify-center rounded-md font-sans font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none';
    
    const variants = {
      primary: 'bg-ledger text-paper hover:bg-ledger/90',
      secondary: 'bg-hairline text-ink hover:bg-hairline/80',
      danger: 'bg-alert text-paper hover:bg-alert/90',
      ghost: 'hover:bg-hairline text-ink-muted hover:text-ink',
    };
    
    const sizes = {
      sm: 'h-8 px-3 text-caption',
      md: 'h-10 px-4 text-body',
      lg: 'h-12 px-6 text-body',
    };

    return (
      <button
        ref={ref}
        className={`${baseStyles} ${variants[variant]} ${sizes[size]} ${className}`}
        {...props}
      />
    );
  }
);
Button.displayName = 'Button';
