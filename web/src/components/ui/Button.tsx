import type { ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'
import { Spinner } from './Spinner'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

// `primary` = l'action primaire d'une vue (fusionne l'ancien ActBtn : variation trop faible pour
// une primitive séparée). Classes littérales par variante (Tailwind scanne le source).
const VARIANT: Record<Variant, string> = {
  primary: 'border-transparent bg-accent-600 text-white hover:bg-accent-500',
  secondary: 'border-border bg-surface-raised text-fg hover:border-border-strong',
  ghost: 'border-transparent bg-transparent text-muted hover:bg-surface-raised hover:text-fg',
  danger: 'border-danger-500/30 bg-danger-500/15 text-danger-500 hover:bg-danger-500/25',
}
const SIZE: Record<Size, string> = {
  sm: 'h-8 gap-1.5 px-3 text-sm',
  md: 'h-10 gap-2 px-4 text-sm',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  busy?: boolean
}

export function Button({
  variant = 'secondary', size = 'md', busy = false, className, children, disabled, ...rest
}: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center rounded-card border font-medium transition-colors',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-500',
        'disabled:pointer-events-none disabled:opacity-50',
        VARIANT[variant], SIZE[size], className,
      )}
      disabled={disabled || busy}
      {...rest}
    >
      {busy && <Spinner className="size-4" />}
      {children}
    </button>
  )
}
