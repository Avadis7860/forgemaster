#!/usr/bin/env node
// Runner host-side du gate Tier-1.5 « feature-verified » : charge une URL en
// Chromium headless, vérifie que des marqueurs (texte FR attendu) sont rendus
// dans le DOM, archive un screenshot. Sortie : un objet JSON sur stdout.
//
// Piloté par feature_verify.py (jamais lancé à la main en prod). Réutilise le
// playwright-core déjà vendoré dans ce dossier + le Chromium de ~/.cache/ms-playwright.
//
// Entrée : argv[2] = JSON {url, markers:[...], screenshot?:path, timeout_ms?:int, cookies?:[...],
//          wait_for_text?:str, wait_timeout_ms?:int, clicks?:[...], after_markers?:[...]}
//          (ou le même JSON sur stdin si argv[2] absent).
//          cookies (optionnel) : tableau Playwright {name,value,url|domain+path,...} injecté dans
//          le contexte AVANT le goto → permet de rendre une UI derrière auth (deep-link authentifié),
//          ce que goto-only seul ne peut pas atteindre. N'EXÉCUTE rien : ouvre/rend seulement.
//          wait_for_text (optionnel) : sonde le DOM jusqu'à voir ce texte AVANT de screenshoter — pour une
//          surface dont le contenu arrive APRÈS networkidle (WS : terminal PTY, transcript). networkidle
//          ne suit pas les WebSockets → sans ce repère la capture serait vide. Absent → comportement inchangé.
//          clicks (optionnel) : séquence de gestes READ-ONLY joués APRÈS le goto, AVANT le screenshot, pour
//          rendre une UI derrière un clic (panneau/onglet/basculeur piloté par un state React, sans route
//          → inatteignable au goto seul). Chaque geste = {text, nth?, wait_ms?} : clique le nᵉ élément dont
//          le texte contient `text`. USAGE STRICT : n'ouvrir que des surfaces READ-ONLY (détail, aperçu,
//          historique) — JAMAIS un geste à effet mutant/destructif (submit, delete, dispatch). Absent →
//          comportement inchangé (goto-only pur).
//          after_markers (optionnel) : preuve DEUX-TEMPS d'un jalon jouable. Si présent, le runner capture
//          l'état AT-REST avant les gestes, joue `clicks`, puis exige que ces markers apparaissent APRÈS le
//          geste ET qu'ils étaient ABSENTS at-rest — prouve une TRANSITION d'état observable, pas une simple
//          présence. Dans ce mode, `markers` reste la cible AT-REST (le cadre déjà rendu au chargement).
// Sortie : {url, ok, found:[], missing:[], after_found:[], after_missing:[], pre_present:[], title,
//          screenshot, clicks?:[...], error?}
//   ok (legacy, sans after_markers) = tous les markers présents (page chargée).
//   ok (deux-temps) = markers at-rest présents ET after_markers présents après geste ET AUCUN d'eux présent
//   at-rest (pre_present vide = transition prouvée) ET tous les clics réussis.

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');

function readStdin() {
  return new Promise((resolve) => {
    let d = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => (d += c));
    process.stdin.on('end', () => resolve(d));
  });
}

// Fallback : si playwright-core ne résout pas l'exécutable via son registre,
// on cherche le chrome installé dans le cache ms-playwright (revision pinée).
function findChromeFallback() {
  const base = path.join(process.env.HOME || '', '.cache', 'ms-playwright');
  if (!fs.existsSync(base)) return null;
  // playwright headless veut chrome-headless-shell ; à défaut on retombe sur le
  // Chromium complet (supporte --headless). Layouts connus : chrome-linux64/ (récent)
  // et chrome-linux/ (ancien).
  for (const d of fs.readdirSync(base)) {
    if (!d.startsWith('chromium')) continue;
    for (const sub of ['chrome-linux64/chrome', 'chrome-linux/chrome',
                       'chrome-headless-shell-linux64/chrome-headless-shell']) {
      const exe = path.join(base, d, sub);
      if (fs.existsSync(exe)) return exe;
    }
  }
  return null;
}

async function launch() {
  try {
    return await chromium.launch({ headless: true });
  } catch (e) {
    const exe = findChromeFallback();
    if (!exe) throw e;
    return await chromium.launch({ headless: true, executablePath: exe });
  }
}

async function main() {
  const raw = process.argv[2] || (await readStdin());
  const input = JSON.parse(raw);
  const markers = Array.isArray(input.markers) ? input.markers : [];
  const timeout = input.timeout_ms || 15000;
  const out = { url: input.url, ok: false, found: [], missing: [], after_found: [], after_missing: [],
                pre_present: [], title: null, screenshot: input.screenshot || null };
  const browser = await launch();
  try {
    // viewport (optionnel) : {width,height} pour capturer une largeur donnée (desktop/mobile) ; défaut
    // Playwright (~1280×720). full_page (optionnel) : capture toute la hauteur scrollable, pas juste le
    // viewport — utile pour une page longue ou un DAG qui déborde.
    const vp = input.viewport && input.viewport.width && input.viewport.height
      ? { width: input.viewport.width | 0, height: input.viewport.height | 0 } : null;
    const context = await browser.newContext(vp ? { viewport: vp } : {});
    if (Array.isArray(input.cookies) && input.cookies.length) {
      await context.addCookies(input.cookies);
    }
    const page = await context.newPage();
    await page.goto(input.url, { waitUntil: 'domcontentloaded', timeout });
    // laisse une app cliente finir son rendu, sans bloquer si jamais idle
    await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
    // surface async/WS : le contenu arrive après networkidle → sonde le repère, puis court répit pour
    // laisser un peu plus de flux se peindre (prompt shell après la bannière, etc.).
    if (input.wait_for_text) {
      await page.waitForFunction(
        (needle) => (document.body ? document.body.innerText : '').includes(needle),
        input.wait_for_text, { timeout: input.wait_timeout_ms || 8000 },
      ).catch(() => {});
      await page.waitForTimeout(600);
    }
    // Preuve DEUX-TEMPS (jalon jouable) : after_markers déclaré → capturer l'état AT-REST AVANT tout geste
    // (baseline). `markers` = cadre at-rest ; on note les after_markers déjà visibles at-rest (pre_present :
    // le geste ne les cause pas ⇒ transition non prouvée). Sans after_markers → legacy inchangé.
    const afterMarkers = Array.isArray(input.after_markers) ? input.after_markers : [];
    const twoPhase = afterMarkers.length > 0;
    if (twoPhase) {
      const titleBefore = await page.title();
      const textBefore = await page.evaluate(() => (document.body ? document.body.innerText : ''));
      const hayBefore = `${titleBefore || ''}\n${textBefore}`;
      for (const m of markers) {
        (hayBefore.includes(m) ? out.found : out.missing).push(m);
      }
      for (const m of afterMarkers) {
        if (hayBefore.includes(m)) out.pre_present.push(m);
      }
    }
    // gestes read-only (clicks) : ouvrir des surfaces pilotées par un state React (sans route). Joués en
    // séquence ; chaque geste attend son court répit pour laisser le panneau se peindre. Échec d'un clic
    // (élément absent) → consigné dans out.clicks, sans planter (la capture montrera l'état atteint).
    if (Array.isArray(input.clicks) && input.clicks.length) {
      out.clicks = [];
      for (const step of input.clicks) {
        const label = String(step.text != null ? step.text : step.selector || '');
        try {
          const loc = step.selector
            ? page.locator(step.selector)
            : page.getByText(step.text, { exact: Boolean(step.exact) });
          await loc.nth(step.nth || 0).click({ timeout: step.timeout_ms || 4000 });
          await page.waitForTimeout(step.wait_ms || 400);
          out.clicks.push({ step: label, ok: true });
        } catch (e) {
          out.clicks.push({ step: label, ok: false, error: String((e && e.message) || e) });
        }
      }
    }
    out.title = await page.title();
    const text = await page.evaluate(() => (document.body ? document.body.innerText : ''));
    const hay = `${out.title || ''}\n${text}`;   // état APRÈS les gestes
    if (twoPhase) {
      for (const m of afterMarkers) {
        (hay.includes(m) ? out.after_found : out.after_missing).push(m);
      }
    } else {
      for (const m of markers) {
        (hay.includes(m) ? out.found : out.missing).push(m);
      }
    }
    if (input.screenshot) {
      fs.mkdirSync(path.dirname(input.screenshot), { recursive: true });
      await page.screenshot({ path: input.screenshot, fullPage: Boolean(input.full_page) });
    }
    // Legacy : ok ssi markers présents. Deux-temps : + after_markers présents après geste, + aucun déjà
    // présent at-rest (transition prouvée), + tous les clics réussis (un clic raté = preuve non jouée).
    const clicksAllOk = !Array.isArray(out.clicks) || out.clicks.every((c) => c.ok);
    out.ok = out.missing.length === 0
      && (!twoPhase || (out.after_missing.length === 0 && out.pre_present.length === 0 && clicksAllOk));
  } catch (e) {
    out.error = String((e && e.message) || e);
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(out));
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String((e && e.message) || e) }));
  process.exit(1);
});
