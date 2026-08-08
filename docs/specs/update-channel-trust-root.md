# spec — racine de confiance du canal de mise à jour (signature Ed25519)

> **Contrat à implémenter, pas comportement livré.** Aucune vérification de signature n'existe dans le produit
> à ce jour ; ce document fixe ce qui est verrouillé **avant** qu'une ligne de crypto soit écrite, pour que la
> phase qui l'écrira n'ait pas à improviser un modèle de confiance.
> Cibles prévues : `update.py` (le canal), `_keys/` (le jeu de clés embarqué), `build_provenance.py`
> (`edition`, qui répond déjà « quelle édition tourne ici ? »).

## Problème tranché

Le canal de mise à jour doit apprendre à une instance qu'une version existe. Cette instance n'a **pas** notre
miroir git, **pas** notre trousseau, et personne à appeler : elle ne peut fonder sa confiance que sur ce
qu'elle transporte déjà. Poser un wheel qu'on lui désigne à la main est légitime **parce que** c'est local et
hors-ligne ; dès qu'une référence arrive par le réseau, l'absence de signature n'est plus une simplification.

Le produit porte déjà un module de signature — `secrets/jwt.py` (`mint_hs256`) — et il est **inutilisable ici**.
Il est **symétrique** : vérifier une signature HS256 exige de détenir le secret qui la produit. Distribuer le
vérificateur reviendrait à distribuer le faussaire. HS256 reste correct là où il vit (deux parties qui
détiennent toutes deux le secret par droit) ; il est disqualifié dès que le vérificateur est un inconnu.

## Règles verrouillées

1. **Signature asymétrique Ed25519, via `cryptography`** — déjà une dépendance runtime du produit (plancher
   `>=42`, posée pour le Fernet du `EncryptedFileStore`). **Aucune dépendance nouvelle.** Ed25519 plutôt que
   RSA parce qu'il n'a **aucun paramètre à mal configurer** : ni taille de clé, ni padding, ni choix de hash.
2. **Enveloppe signée d'un seul fichier**, jamais une signature détachée à côté. Le manifeste voyage comme un
   payload **encodé** accompagné de ses signatures dans le même document. Deux raisons, la seconde étant la
   vraie :
   - **aucune canonicalisation n'entre dans la vérification** — un payload opaque supprime la question de
     l'ordre des clés, des espaces et de l'échappement au lieu de la résoudre ;
   - **une publication en deux fichiers n'est pas atomique** — un client peut tirer le nouveau manifeste avec
     l'ancienne signature et conclure « invalide » sur une paire simplement mal assortie. Un faux négatif
     d'alarme de sécurité est pire que son absence : il apprend à l'ignorer.
3. **On vérifie des OCTETS, jamais un objet re-sérialisé**, et **parse-after-verify** sans exception : le
   payload n'est parsé qu'**après** que sa signature a vérifié. Parser d'abord ferait du parseur la première
   surface d'attaque, exécutée avant tout contrôle.
4. **Un JEU de clés, et une LISTE de signatures dès le premier jour** — même quand elle n'a qu'un élément. Le
   produit embarque une **liste** de clés publiques acceptées ; le manifeste porte une **liste** de signatures.
   C'est ce qui rend la rotation possible (§7), et c'est **la seule pièce de format verrouillée d'avance** :
   un champ scalaire ne devient pas une liste sans casser le format chez tous ceux qui l'ont déjà lu.
5. **`key_id` DÉRIVÉ de la clé** — préfixe du SHA-256 de ses octets bruts. Jamais un compteur, jamais une date :
   un identifiant dérivé ne peut pas **mentir** sur la clé qu'il désigne, et une rotation n'a pas d'ordre imposé.
6. **La vérification exige les DEUX conditions** : que le `key_id` annoncé soit **dans le jeu embarqué**, *et*
   que la signature vérifie **sous cette clé-là**. **Jamais** un « essaie toutes les clés » — cette commodité
   transforme une incohérence (un manifeste qui se réclame d'une clé et est signé par une autre) en **succès
   silencieux**.
7. **La clé publique est une pièce de l'ÉDITION.** Elle vit dans `src/forgemaster/_keys/`, **source suivi par
   git** — elle entre donc par la porte `packages` de `hatch`, **pas** par `force_include` (réservé aux
   artefacts de build sous garde d'inventaire, cf. `hatch_build.py`). Elle arrive chez l'utilisateur par le
   chemin qu'il a **déjà** accordé pour installer le produit : aucun tirage réseau, aucune fenêtre de premier
   démarrage. Écartés : le TOFU au premier boot (le premier tirage est exactement le moment où un attaquant
   gagne) et la clé fournie en configuration (elle impose à l'utilisateur la gestion de clés qu'on cherche à
   lui épargner, et dégrade silencieusement en « aucune MAJ, jamais »).
   **Conséquence assumée, à ne pas traiter comme un défaut : la racine de confiance ne peut pas être plus
   fraîche que le binaire.**
8. **La clé privée n'existe nulle part dans ce dépôt, ni dans aucun runner.** Elle est détenue hors ligne par
   le mainteneur ; la signature d'une release ne se fait pas en CI. Le dépôt ne contient que des clés
   **publiques**, et leur publicité est leur fonction : n'importe qui peut auditer la racine de confiance de
   l'édition qu'il exécute.
9. **Le canal ANNONCE, il ne télécharge pas.** Le manifeste signé déclare « la version X existe, voici le
   SHA-256 de son wheel » ; le produit ne tire **aucun binaire**. Le dépôt de wheels calcule déjà le SHA-256 de
   ce qu'on lui remet (`update.stage_wheel`) : c'est là que l'annonce signée se confronte à l'artefact réel.
   **Rayon d'explosion d'une clé volée : une notification mensongère, pas une exécution.**
10. **Aucun réseau dans `update apply`.** La signature gate la **proposition** ; le verbe, lui, pose le fichier
    qu'on lui désigne et rien d'autre. Ce sont deux pièces, et cette frontière ne se rouvre pas.
11. **Aucune application automatique**, y compris pour un correctif de sécurité. C'est cette règle qui fait
    qu'un vol de clé n'est pas un vol d'instance.
12. **Conduite en dégradé — reprise de `apply_update` verbatim, pas réinventée** : *absence n'est pas panne ;
    un juge **présent** qui plante est un ÉCHEC.*
    - édition **sans** jeu de clés (checkout de dev, édition antérieure au câblage) → **ne propose rien** et
      le **dit**. Capacité absente, pas panne ;
    - vérificateur **présent** qui échoue → **échec**, bruyant côté log ;
    - `key_id` **inconnu** ≠ signature **invalide**. Le premier dit « une rotation a eu lieu, ou quelqu'un
      sonde » ; le second dit « ces octets ne sont pas de nous ». Les confondre fait perdre le seul indicateur
      de compromission qu'un système hors-ligne aura jamais.

## Rotation et révocation

**Rotation, trois mouvements** — le jeu de clés est **plat** (pas de racine déléguante, pas de chaîne) :

1. publier une édition dont le jeu vaut `{ancienne, nouvelle}` ; on signe encore avec l'ancienne ;
2. une fois cette édition répandue, signer avec **les deux** (d'où la liste de la règle 4) : les deux
   populations vérifient, personne ne décroche ;
3. publier une édition dont le jeu vaut `{nouvelle}`, et cesser de signer avec l'ancienne.

**Le coût est nommé, pas caché** : une instance qui n'a mis à jour à **aucun** moment du chevauchement devient
**sourde** — elle ne voit plus rien passer. Elle n'est jamais **trompée** ; elle est muette. Son rattrapage
existe et n'a besoin d'aucune racine de confiance : `forgemaster update apply <wheel>`, le chemin manuel, qui
n'a pas de réseau par construction. *La voie hors-ligne est la voie de secours de la voie en ligne.*

**Révocation : il n'y a PAS de liste de révocation, et c'est délibéré.** Une CRL exigerait le réseau qu'on
refuse et régresserait sur « qui signe la révocation ? » — donc sur une deuxième racine, avec le même problème.
Révoquer se fait en trois gestes : ① cesser de signer avec la clé compromise ② publier une édition dont le jeu
l'exclut ③ annoncer hors bande. Ce dénuement n'est tenable que **parce que** les règles 9 et 11 bornent ce
qu'une clé volée permet.

## Invariants de test (ce que la phase d'implémentation devra prouver)

- **Un manifeste dont le `key_id` n'est pas dans le jeu embarqué est refusé** — même si sa signature est
  cryptographiquement valide sous une clé qu'on ne connaît pas.
- **Un manifeste signé par une clé du jeu mais annonçant le `key_id` d'une AUTRE clé du jeu est refusé.**
  C'est le test qui interdit le « essaie toutes les clés » : il échoue si la vérification cherche la bonne clé
  au lieu d'exiger celle qui est annoncée.
- **Un octet modifié dans le payload invalide la signature** — le test doit muter le payload **encodé**, pas
  l'objet, pour prouver que la vérification porte bien sur les octets reçus.
- **Le payload n'est jamais parsé quand la signature échoue** — vérifiable par un payload **syntaxiquement
  invalide** accompagné d'une mauvaise signature : le verdict doit être « signature invalide », jamais une
  erreur de parsing.
- **Un manifeste à DEUX signatures vérifie pour un jeu ne contenant que l'une OU que l'autre des deux clés** —
  c'est le chevauchement de rotation, et sans ce test la règle 4 n'est qu'une intention.
- **Une édition sans jeu de clés ne propose rien et ne lève rien** — elle rend un motif lisible.
- **Un `key_id` inconnu et une signature invalide produisent des verdicts DISTINCTS** dans le log.
- **Aucun accès réseau sur le chemin de `update apply`.** À écrire, et il faut le dire : **aucun test
  n'assure cette propriété aujourd'hui**. Elle est tenue par le contrat du module (`update.py`, docstring) et
  par l'**étroitesse** de ce qu'il appelle — ce qui est une intention, pas une garde. Le seul garde-fou
  automatique voisin est la **pureté stdlib** de l'applicateur (`test_apply_ne_depend_de_rien_du_forgemaster`,
  vérifiée par AST), qui interdit un import, pas une socket. La phase qui branchera le canal doit donc
  **ajouter** cette garde, pas s'appuyer dessus.

## Ce que cette spec ne tranche pas

L'**hébergement** du manifeste, son **schéma complet** au-delà de l'enveloppe et de la liste de signatures, la
**lecture périodique** (démarrage puis intervalle, sans jamais bloquer le daemon) et le **verdict d'interface**
appartiennent aux phases suivantes du canal.
