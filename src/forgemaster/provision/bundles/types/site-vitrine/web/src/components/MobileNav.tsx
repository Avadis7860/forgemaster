// MobileNav.tsx — îlot React interactif (menu mobile). Hydraté à la demande (`client:visible` côté layout) :
// c'est une VRAIE interactivité (toggle + clavier), donc une hydratation justifiée. Anime avec `motion` en
// respectant `prefers-reduced-motion`. a11y : bouton `aria-expanded`/`aria-controls`, menu `role=menu`.
import { useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';

interface Props {
  label: string;
  homeLabel: string;
  homeHref: string;
}

export default function MobileNav({ label, homeLabel, homeHref }: Props) {
  const [open, setOpen] = useState(false);
  const reduce = useReducedMotion();

  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="mobile-menu"
        onClick={() => setOpen((o) => !o)}
      >
        {label}
      </button>
      {open && (
        <motion.ul
          id="mobile-menu"
          role="menu"
          initial={reduce ? false : { opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: reduce ? 0 : 0.18 }}
        >
          <li role="none">
            <a role="menuitem" href={homeHref}>
              {homeLabel}
            </a>
          </li>
        </motion.ul>
      )}
    </div>
  );
}
