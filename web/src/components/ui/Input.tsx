import type { InputHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

/** Champ texte de base, aligné sur les tokens (hauteur/rayon/focus). */
export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'h-10 w-full rounded-card border border-border bg-bg px-3 text-sm text-fg placeholder:text-faint',
        'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-500',
        className,
      )}
      {...rest}
    />
  )
}
