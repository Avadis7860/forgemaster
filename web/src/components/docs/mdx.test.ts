import { describe, expect, it } from 'vitest'
import { firstHeading, stripMdxDirectives } from './mdx'

describe('stripMdxDirectives', () => {
  it('retire les import/export MDX de tête, garde le contenu', () => {
    const md = "import { Tabs } from 'fumadocs-ui/components/tabs'\nexport const x = 1\n# Titre\ncorps"
    expect(stripMdxDirectives(md)).toBe('# Titre\ncorps')
  })
  it('préserve un import DANS un bloc de code fencé', () => {
    const md = '# T\n```ts\nimport z from "zod"\n```\n'
    expect(stripMdxDirectives(md)).toContain('import z from "zod"')
  })
  it('ne touche pas une prose sans directive', () => {
    const md = '# T\nune phrase normale importante'   // "importante" ≠ "import "
    expect(stripMdxDirectives(md)).toBe(md)
  })
})

describe('firstHeading', () => {
  it('extrait le 1er titre ATX', () => {
    expect(firstHeading('import x from "y"\n# Defining schemas\ncorps')).toBe('Defining schemas')
  })
  it('null si aucun titre', () => {
    expect(firstHeading('juste du corps')).toBeNull()
  })
})
