import type { ComponentPropsWithoutRef, ElementType, ReactNode } from 'react'
import { cn } from '@/lib/cn'

// Primitive POLYMORPHE via `as` : préserve la sémantique HTML (div/li/section/form/article).
// Recette de classes figée ici ; le layout variable est poussé par l'appelant en `className`.
type CardOwnProps = { raised?: boolean; className?: string; children?: ReactNode }

export function Card<T extends ElementType = 'div'>({
  as, raised = false, className, children, ...rest
}: { as?: T } & CardOwnProps & Omit<ComponentPropsWithoutRef<T>, keyof CardOwnProps | 'as'>) {
  const Tag = (as ?? 'div') as ElementType
  return (
    <Tag className={cn('rounded-card border border-border bg-surface', raised && 'shadow-raised', className)} {...rest}>
      {children}
    </Tag>
  )
}
