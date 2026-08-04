# spec — feature-verified (gate e2e)

> Contrainte distillée (vault `decisions/projects/2026-06-22--feature-verified-gate.md`).
> Cible : `gate/verify.py` (+ `gate/review.py` pour l'ancre SHA). Refactor #10/#13.

## Problème tranché

Aucun étage ne prouvait que **le résultat métier s'affiche réellement** : deploy vert / hash d'asset
correct / endpoint 200 ne disent rien du fait que le titre/texte attendu est **rendu dans le DOM**.
Faux-vert récurrent, leçon jamais distillée en artefact exécutable.

## Règles verrouillées

1. La porte dure est **déterministe** ; **le LLM ne juge ni ne fixe le seuil** (le rendu se **prouve**).
2. Scoper aux **cibles déclarées du diff** (lignes introduites), **jamais le pré-existant** ; périmètre
   déclenché par un trigger **hybride** `has_visual_change(files, diff_text)` — un **vrai** changement visuel,
   pas tout fichier front : (a) STYLE touché (`.css/.scss/…`, par nom) → visuel ; (b) fichier front sous un
   dossier RENDU (`pages/`/`components/`/`routes/`/`layouts/`/`views/`, par nom) → visuel ; (c) fichier front
   ailleurs (App.tsx root, `lib/`) → visuel **seulement si ses lignes ajoutées introduisent du markup** (`</`,
   `/>`, `className=` — marqueurs conservateurs, robustes aux génériques TS). Un `.tsx` de câblage/type/contrat
   (aucun markup) est **non-visuel** → Tier-1.5 N/A, couvert par la review Tier-1. (`has_ui()` name-only reste
   le prédicat coarse de surface front ; le trigger du gate est `has_visual_change`.)
3. **Fail-CLOSED** pour l'irréversible : UI touchée + preuve absente/périmée/rouge → merge **bloqué**. Un
   acte outward (merge/destroy/deploy) exige un **feu vert humain**, jamais autonome.
4. Un override **Tier-1.5 (🟡)** peut lever du 🟡 ; **jamais** un 🔴 Tier-0 (conflit/secret/syntaxe).
   Override **humain, explicite, tracé** (raison non vide, consignée).
5. Gate d'entrée ≠ gate de sortie : les tiers **s'ajoutent et se couvrent** (additif, N/A-safe, sans
   réécrire les tiers existants).
6. Contrat/manifeste = **source de vérité extraite au runtime**, jamais un enum en dur.
7. La promesse du gate est **prouvée live (dogfood sur sa propre PR)** ou ne vaut rien.
8. **Preuve DEUX-TEMPS pour un jalon jouable** (`CONTRACT_VERSION = feature-verify-v2`) : « le résultat se
   **joue** » ≠ « du texte est rendu au chargement ». Quand le worker déclare un bloc `interaction`
   (`clicks` read-only + `after_markers`) dans `.forgemaster/verify-markers.json`, la porte capture l'état
   **at-rest**, joue les gestes, puis exige que les `after_markers` soient **présents APRÈS le geste ET
   absents at-rest** (`pre_present` non vide ⇒ 🔴, + un clic déclaré en échec ⇒ 🔴). Un `after_marker` déjà
   rendu au chargement est un **label statique**, pas une transition — la preuve est *verified*, pas
   *trusted*. La garde **read-only** (jamais submit/delete/dispatch) reste doctrinale (METHOD + runner), non
   enforcée runtime (un denylist ferait des faux-négatifs sur des libellés légitimes). Extension **additive** :
   sans bloc `interaction`, comportement at-rest inchangé. Bump `v1→v2` : un verdict `feature-verify-v1` est
   traité **non frais** (`is_fresh`) → re-gate sous la sémantique deux-temps.

- **Ancre = SHA de HEAD** : le verdict persiste `reviewed_sha` ; frais ssi `reviewed_sha == HEAD` **ET**
  `contract_version == CONTRACT_VERSION` → tout commit ultérieur (ou contrat périmé) **périme** la preuve.
- **« Jamais blanchi »** : échec d'exécution (node absent, browser ko, timeout) → `{ok: False, error}`,
  **jamais vert**. **N/A-safe** : pas d'UI touchée → aucun blocker ajouté.

## Invariants de test (à encoder dans forgemaster)

- UI touchée + marqueurs absents du DOM → gate **bloque** ; marqueurs présents → passe (screenshot non
  vide + texte attendu lu).
- node/browser/timeout → `ok=False`, jamais exception silencieuse ni vert.
- Un commit après verdict **périme** la preuve (`is_fresh` faux).
- Task sans surface UI → **aucun** blocker Tier-1.5 (N/A-safe).
- `override` lève **uniquement** Tier-1.5, jamais un Tier-0.
- Jalon jouable : `after_marker` absent at-rest + présent après le geste → **passe** ; jamais apparu
  (`after_missing`) → **bloque** ; déjà présent at-rest (`pre_present`) → **bloque** (transition non prouvée) ;
  clic déclaré en échec → **bloque** (preuve non jouée).
- Rétro-compat : un contrat legacy `{"markers":[…]}` (sans `interaction`) garde la sémantique at-rest.
- Un verdict `feature-verify-v1` au SHA courant est **non frais** (bump v2 durci).
