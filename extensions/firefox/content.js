"use strict";

const MAX_COMPONENTS = 10;
const MAX_INSPECTED_ELEMENTS = 5000;
const MAX_REPORTED_ELEMENTS = 60;
const MAX_CANDIDATES = 30;
const NUMERIC_TEXT = /^(?:(\d{1,8}(?:[.,]\d{1,6})?)\s*([x×])?|([x×])\s*(\d{1,8}(?:[.,]\d{1,6})?))$/i;
const DECIMAL_MULTIPLIER = /^\d{1,8}(?:\.\d{1,4})?$/;
const NUMERIC_CANDIDATE = /^\d{1,8}(?:[.,]\d{1,6})?$/;

// Doit rester cohérent avec les motifs HTTPS du manifest.json (host_permissions et content_scripts).
// crash.aviator.studio : domaine du document de l'iframe du jeu, relevé dans l'onglet Réseau de Firefox
// (document de type « subdocument » chargé depuis la page Aviator de Bet261).
const ALLOWED_HOST_SUFFIXES = ["bet261.mg", "crash.aviator.studio"];

function isAllowedHost(hostname) {
  const host = String(hostname || "").toLowerCase();
  return ALLOWED_HOST_SUFFIXES.some((suffix) => host === suffix || host.endsWith(`.${suffix}`));
}

function isValidDisplayedMultiplier(value) {
  if (typeof value !== "string" || !DECIMAL_MULTIPLIER.test(value)) return false;
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) >= 1n && BigInt(whole) <= 99999999n && fraction.length <= 4;
}

function possibleDomIdentifiers(element) {
  return Array.from(element.attributes || [])
    .filter((attribute) => /^(?:id|data-id|data-round-id|data-game-id|data-match-id)$/i.test(attribute.name))
    .map((attribute) => ({
      attribute: attribute.name,
      value: /^[A-Za-z0-9_-]{1,80}$/.test(attribute.value) ? attribute.value : null
    }));
}

function findComponentIndex(node, accessibleElements) {
  const hosts = accessibleElements.filter((element) =>
    String(element.tagName || "").toLowerCase() === "hg-aviator-llc-game"
    && safeClassTokens(element).includes("aviator-llc-game")
  );
  for (let current = node; current;) {
    const index = hosts.indexOf(current);
    if (index >= 0) return index + 1;
    if (current.parentElement) current = current.parentElement;
    else {
      const root = current.getRootNode && current.getRootNode();
      current = root && root.host ? root.host : null;
    }
  }
  return null;
}

function safeClassTokens(element) {
  const raw = typeof element.className === "string" ? element.className : "";
  return raw.split(/\s+/).filter((token) => /^[A-Za-z_-][A-Za-z0-9_-]{0,31}$/.test(token)).slice(0, 4);
}

function safeAttribute(element, name, pattern) {
  const value = element && element.getAttribute ? element.getAttribute(name) : null;
  return typeof value === "string" && pattern.test(value) ? value : null;
}

function elementPath(element) {
  const path = [];
  let current = element;
  for (let depth = 0; current && depth < 12; depth += 1) {
    path.push({
      tag: String(current.tagName || "unknown").toLowerCase(),
      classes: safeClassTokens(current),
      role: safeAttribute(current, "role", /^[A-Za-z-]{1,32}$/)
    });
    if (current.parentElement) current = current.parentElement;
    else {
      const root = current.getRootNode && current.getRootNode();
      current = root && root.host ? root.host : null;
    }
  }
  return path;
}

function countElements(doc) {
  try { return doc.getElementsByTagName("*").length; } catch (_error) { return null; }
}

function scanOpenDocument(doc, maxElements = MAX_INSPECTED_ELEMENTS) {
  const elements = [];
  const shadowRoots = [];
  const visited = new Set();
  const stack = [doc.documentElement || doc];
  let truncated = false;
  while (stack.length) {
    const node = stack.pop();
    if (!node || visited.has(node)) continue;
    visited.add(node);
    if (node.nodeType === 1) {
      if (elements.length >= maxElements) { truncated = true; break; }
      elements.push(node);
      const children = Array.from(node.children || []);
      for (let index = children.length - 1; index >= 0; index -= 1) stack.push(children[index]);
      if (node.shadowRoot) {
        shadowRoots.push(node.shadowRoot);
        stack.push(node.shadowRoot);
      }
    } else {
      const children = Array.from(node.children || []);
      for (let index = children.length - 1; index >= 0; index -= 1) stack.push(children[index]);
    }
  }
  return { elements, shadowRoots, inspectedElementCount: elements.length, truncated };
}

function inspectPersonalBets(doc) {
  const containers = Array.from(doc.querySelectorAll('[class*="_bets_1gs3m_"]'));
  const bets = [];
  for (const container of containers) {
    for (const row of Array.from(container.children || [])) {
      const dateText = row.querySelector('span')?.textContent?.trim() || "";
      const betBox = row.querySelector('[class*="_bet_4mla1_"]');
      const winBox = row.querySelector('[class*="_win_4mla1_"]');
      if (!betBox || !winBox) continue;
      const stakeText = betBox.querySelector('span')?.textContent?.trim() || "";
      const multiplierText = betBox.querySelector('[class*="_container_1ckpf_"] span')?.textContent?.trim() || "";
      const returnedText = winBox.querySelector('span')?.textContent?.trim() || "";
      const amount = (value) => /^\d{1,10}(?:[.,]\d{1,4})?$/.test(value) ? value.replace(",", ".") : null;
      const dateMatch = /^(\d{2})-(\d{2})-(\d{2})\s*[·]\s*(\d{2}):(\d{2})$/.exec(dateText);
      if (!dateMatch || !amount(stakeText)) continue;
      const [, day, month, shortYear, hour, minute] = dateMatch;
      const year = Number(shortYear) >= 70 ? 1900 + Number(shortYear) : 2000 + Number(shortYear);
      const localDate = new Date(year, Number(month) - 1, Number(day), Number(hour), Number(minute));
      if (Number.isNaN(localDate.getTime())) continue;
      const cashout = /^([1-9]\d{0,7}(?:[.,]\d{1,4})?)x$/i.exec(multiplierText);
      bets.push({
        bet_at: localDate.toISOString(),
        stake_amount: amount(stakeText),
        cashout_multiplier: cashout ? cashout[1].replace(",", ".") : null,
        returned_amount: amount(returnedText),
        currency: "MGA"
      });
    }
  }
  return bets;
}

function inspectAviatorComponents(doc, limits = {}, accessibleElements = null) {
  const maxElements = limits.maxInspectedElements || MAX_INSPECTED_ELEMENTS;
  const maxReported = limits.maxReportedElements || MAX_REPORTED_ELEMENTS;
  const candidateLimit = limits.maxCandidates || MAX_CANDIDATES;
  const elements = accessibleElements || scanOpenDocument(doc, maxElements).elements;
  const hosts = elements.filter((host) => String(host.tagName || "").toLowerCase() === "hg-aviator-llc-game")
    .filter((host) => safeClassTokens(host).includes("aviator-llc-game"))
    .slice(0, MAX_COMPONENTS);
  const roots = [];
  const components = [];

  for (const [componentIndex, host] of hosts.entries()) {
    const component = {
      index: componentIndex + 1,
      host: { tag: "hg-aviator-llc-game", classes: safeClassTokens(host), role: safeAttribute(host, "role", /^[A-Za-z-]{1,32}$/) },
      shadowRoot: host.shadowRoot ? "open" : "not_exposed_absent_or_closed",
      inspectedElementCount: 0,
      truncated: false,
      openShadowRootCount: 0,
      canvasCount: 0,
      svgCount: 0,
      interestingElements: [],
      numericCandidates: [],
      limits: []
    };
    const visited = new Set();
    const walk = (root, depth) => {
      if (!root || depth > 12 || component.inspectedElementCount >= maxElements) {
        component.truncated = true;
        return;
      }
      if (root.nodeType === 1 && !visited.has(root)) {
        visited.add(root);
        component.inspectedElementCount += 1;
        const tag = String(root.tagName || "unknown").toLowerCase();
        const openShadow = Boolean(root.shadowRoot);
        const rawType = tag === "input" || tag === "button" ? safeAttribute(root, "type", /^[A-Za-z-]{1,20}$/) : null;
        const role = safeAttribute(root, "role", /^[A-Za-z-]{1,32}$/);
        if (tag === "canvas") component.canvasCount += 1;
        if (tag === "svg") component.svgCount += 1;
        if (openShadow) component.openShadowRootCount += 1;

        let value = "";
        if (!root.children || root.children.length === 0) {
          value = String(root.textContent || "").trim();
        }
        const match = value.length <= 24 ? NUMERIC_TEXT.exec(value) : null;
        const item = {
          tag,
          classes: safeClassTokens(root),
          role,
          type: rawType,
          shadowDom: openShadow ? "open" : "not_exposed_absent_or_closed",
          path: elementPath(root)
        };
        const isInteresting = tag === "canvas" || tag === "svg" || openShadow || role || rawType || Boolean(match);
        if (isInteresting && component.interestingElements.length < maxReported) component.interestingElements.push(item);
        if (match && component.numericCandidates.length < candidateLimit) {
          component.numericCandidates.push({
            value,
            category: match[2] || match[3] ? "number_with_x_or_times_marker" : "number_without_multiplier_marker",
            path: item.path
          });
        }
        if (openShadow) {
          roots.push({ node: root.shadowRoot, context: `composant ${componentIndex + 1} shadowRoot` });
          walk(root.shadowRoot, depth + 1);
        }
        for (const child of Array.from(root.children || [])) walk(child, depth + 1);
        return;
      }
      // ShadowRoot and DocumentFragment expose children, but are not elements.
      for (const child of Array.from(root.children || [])) walk(child, depth + 1);
    };
    roots.push({ node: host, context: `composant Aviator ${componentIndex + 1}` });
    walk(host, 0);
    if (!host.shadowRoot) {
      component.limits.push("Aucun shadowRoot ouvert exposé sur l'hôte; absent et fermé ne peuvent pas être distingués.");
    }
    if (component.truncated) component.limits.push("Inspection arrêtée à la limite locale de profondeur ou d'éléments.");
    components.push(component);
  }
  return { found: hosts.length > 0, componentCount: hosts.length, components, roots };
}

function findHistoryContainers(doc, maxElements = MAX_INSPECTED_ELEMENTS, accessibleElements = null, scanTruncated = false) {
  const scan = accessibleElements ? null : scanOpenDocument(doc, maxElements);
  const elements = accessibleElements || scan.elements;
  const containers = elements.filter((node) => safeClassTokens(node).includes("_items_7l84e_35"));
  return {
    containers: containers.slice(0, 20).map((node) => ({
      node,
      componentIndex: findComponentIndex(node, elements)
    })),
    inspectedElementCount: accessibleElements ? accessibleElements.length : scan.inspectedElementCount,
    truncated: scan ? scan.truncated : scanTruncated
  };
}

function inspectMultiplierHistories(doc, limits = {}, accessibleElements = null, scanTruncated = false) {
  const search = findHistoryContainers(doc, limits.maxInspectedElements || MAX_INSPECTED_ELEMENTS, accessibleElements, scanTruncated);
  const containers = search.containers.map(({ node, componentIndex }, containerIndex) => {
    const buttons = Array.from(node.children || []).filter((child) =>
      String(child.tagName || "").toLowerCase() === "button"
    ).slice(0, MAX_CANDIDATES);
    const entries = buttons.map((button, index) => {
      const text = String(button.textContent || "").trim();
      const validMultiplier = text.length <= 20 && isValidDisplayedMultiplier(text);
      return {
        domOrder: index + 1,
        hasNumericText: NUMERIC_CANDIDATE.test(text),
        value: validMultiplier ? text : null,
        validMultiplier,
        possibleDomIdentifiers: possibleDomIdentifiers(button),
        path: elementPath(button)
      };
    });
    return {
      index: containerIndex + 1,
      componentIndex,
      path: elementPath(node),
      possibleDomIdentifiers: possibleDomIdentifiers(node),
      buttonCount: buttons.length,
      entries,
      invalidValueCount: entries.filter((entry) => !entry.validMultiplier).length
    };
  });
  return {
    selectorBasis: "classe observée par l'utilisateur: _items_7l84e_35",
    foundContainerCount: containers.length,
    numericTextCount: containers.reduce((total, container) =>
      total + container.entries.filter((entry) => entry.hasNumericText).length, 0),
    inspectedElementCount: search.inspectedElementCount,
    truncated: search.truncated,
    containers,
    limits: containers.length ? [] : ["Aucun conteneur avec la classe observée n'a été trouvé dans les composants Aviator accessibles."]
  };
}

function createHistoryEvolutionTracker() {
  let previous = null;
  let lastChange = null;
  return (historyReport) => {
    const current = (historyReport.containers || []).map((container) => ({
      componentIndex: container.componentIndex,
      values: container.entries.filter((entry) => entry.validMultiplier).map((entry) => entry.value),
      possibleIdentifiers: container.entries.map((entry) => entry.possibleDomIdentifiers)
    }));
    if (previous === null) {
      previous = current;
      return { baseline: true, changedSincePreviousScan: false, direction: "indéterminé", addedValues: [], removedValues: [], lastChange: null, identifierCandidatesPresent: current.some((list) => list.possibleIdentifiers.some((items) => items.length)) };
    }
    const before = new Map(previous.map((item) => [item.componentIndex, item.values]));
    const directions = [];
    const addedValues = [];
    const removedValues = [];
    for (const item of current) {
      const oldValues = before.get(item.componentIndex) || [];
      const nextValues = item.values;
      if (JSON.stringify(oldValues) === JSON.stringify(nextValues)) continue;
      if (nextValues.length > oldValues.length) {
        const added = nextValues.length - oldValues.length;
        const prepended = JSON.stringify(nextValues.slice(added)) === JSON.stringify(oldValues.slice(0, nextValues.length - added));
        const appended = JSON.stringify(nextValues.slice(0, oldValues.length)) === JSON.stringify(oldValues.slice(0, nextValues.length - added));
        if (prepended && !appended) { directions.push("début"); addedValues.push(...nextValues.slice(0, added)); }
        else if (appended && !prepended) { directions.push("fin"); addedValues.push(...nextValues.slice(oldValues.length)); }
        else directions.push("ambiguë");
      } else if (oldValues.length > nextValues.length) {
        const removed = oldValues.length - nextValues.length;
        const removedFromStart = JSON.stringify(oldValues.slice(removed)) === JSON.stringify(nextValues);
        const removedFromEnd = JSON.stringify(oldValues.slice(0, nextValues.length)) === JSON.stringify(nextValues);
        directions.push(removedFromStart && !removedFromEnd ? "retrait-au-début" : removedFromEnd && !removedFromStart ? "retrait-à-la-fin" : "remplacement-ou-réordonnancement");
        removedValues.push(...oldValues.filter((value, index) => removedFromStart ? index < removed : index >= nextValues.length));
      } else if (oldValues.length > 1) {
        const prependedAndTrimmed = JSON.stringify(nextValues.slice(1)) === JSON.stringify(oldValues.slice(0, -1));
        const appendedAndTrimmed = JSON.stringify(nextValues.slice(0, -1)) === JSON.stringify(oldValues.slice(1));
        if (prependedAndTrimmed && !appendedAndTrimmed) { directions.push("début-avec-retrait-en-fin"); addedValues.push(nextValues[0]); removedValues.push(oldValues.at(-1)); }
        else if (appendedAndTrimmed && !prependedAndTrimmed) { directions.push("fin-avec-retrait-au-début"); addedValues.push(nextValues.at(-1)); removedValues.push(oldValues[0]); }
        else directions.push("réordonnancement-ou-remplacement");
      } else {
        directions.push("réordonnancement-ou-remplacement");
      }
    }
    previous = current;
    const uniqueDirections = [...new Set(directions)];
    const direction = uniqueDirections.length === 1 ? uniqueDirections[0] : uniqueDirections.length ? "mixte-ou-ambiguë" : "aucun-changement";
    if (direction !== "aucun-changement") {
      lastChange = {
        observedAt: new Date().toISOString(),
        direction,
        addedValues: addedValues.slice(0, MAX_CANDIDATES),
        removedValues: removedValues.slice(0, MAX_CANDIDATES)
      };
    }
    return {
      baseline: false,
      changedSincePreviousScan: direction !== "aucun-changement",
      direction,
      addedValues: addedValues.slice(0, MAX_CANDIDATES),
      removedValues: removedValues.slice(0, MAX_CANDIDATES),
      lastChange,
      identifierCandidatesPresent: current.some((list) => list.possibleIdentifiers.some((items) => items.length)),
      note: "Comparaison d'instantanés DOM seulement; une variation de liste ne prouve ni une fin de manche ni l'identité d'une manche."
    };
  };
}

const HISTORY_MIN_OVERLAP = 10;
const MAX_TRANSITIONS = 50;

/**
 * Compare deux instantanés successifs d'une liste d'historique (valeurs affichées, plus récente d'abord)
 * en supposant l'hypothèse « k nouvelles valeurs insérées au début, les plus anciennes sorties par la fin ».
 * Cette hypothèse est ici une HYPOTHÈSE À VÉRIFIER par l'observation, pas une règle établie du jeu.
 *
 * kind :
 *   no_change   : listes identiques (limite connue : une insertion qui reproduit exactement la même
 *                 fenêtre, possible seulement si la liste est périodique, serait invisible).
 *   shift       : un seul décalage k est compatible; addedValues = les k valeurs apparues (plus récente d'abord).
 *   ambiguous   : plusieurs décalages sont compatibles (valeurs répétées); on ne choisit pas.
 *   unalignable : aucun décalage n'est compatible avec un recouvrement suffisant (trou, rechargement, remplacement).
 */
function alignHistoryWindow(previousValues, nextValues, minOverlap = HISTORY_MIN_OVERLAP) {
  const prev = Array.isArray(previousValues) ? previousValues : [];
  const next = Array.isArray(nextValues) ? nextValues : [];
  if (prev.length === next.length && prev.every((value, index) => value === next[index])) {
    return { kind: "no_change", shift: 0, addedValues: [], candidateShifts: [0] };
  }
  const candidateShifts = [];
  for (let k = 1; k <= next.length; k += 1) {
    const overlap = Math.min(next.length - k, prev.length);
    if (overlap < Math.min(minOverlap, prev.length, next.length)) break;
    let matches = true;
    for (let i = 0; i < overlap; i += 1) {
      if (next[k + i] !== prev[i]) { matches = false; break; }
    }
    if (matches) candidateShifts.push(k);
  }
  if (candidateShifts.length === 1) {
    const k = candidateShifts[0];
    return { kind: "shift", shift: k, addedValues: next.slice(0, k), candidateShifts };
  }
  if (candidateShifts.length > 1) return { kind: "ambiguous", shift: null, addedValues: [], candidateShifts };
  return { kind: "unalignable", shift: null, addedValues: [], candidateShifts: [] };
}

/**
 * Journal borné des transitions de l'historique, pour établir par l'observation si les nouvelles valeurs
 * arrivent bien une par une, au début de la liste, sans modification des valeurs déjà affichées.
 * `observedAt` est l'instant où le diagnostic a vu le changement, pas l'instant de la fin de manche.
 * Rien n'est envoyé à l'API ni à SQLite.
 */
function createHistoryTransitionLog(maxEntries = MAX_TRANSITIONS, now = () => new Date().toISOString()) {
  let previous = null;
  let baselineAt = null;
  let snapshotCount = 0;
  const transitions = [];
  const counters = { shift: 0, shiftOfOne: 0, shiftOfMoreThanOne: 0, ambiguous: 0, unalignable: 0 };
  return (historyReport) => {
    const container = ((historyReport && historyReport.containers) || [])[0];
    if (!container) {
      return { status: "no_history_container", snapshotCount, counters, transitions: transitions.slice() };
    }
    const values = container.entries.filter((entry) => entry.validMultiplier).map((entry) => entry.value);
    snapshotCount += 1;
    if (previous === null) {
      previous = values;
      baselineAt = now();
      return { status: "baseline_recorded", baselineAt, windowSize: values.length, snapshotCount, counters, transitions: [] };
    }
    const result = alignHistoryWindow(previous, values);
    if (result.kind !== "no_change") {
      if (result.kind === "shift") {
        counters.shift += 1;
        if (result.shift === 1) counters.shiftOfOne += 1; else counters.shiftOfMoreThanOne += 1;
      } else counters[result.kind] += 1;
      transitions.push({
        observedAt: now(),
        kind: result.kind,
        shift: result.shift,
        addedValues: result.addedValues,
        candidateShifts: result.candidateShifts.slice(0, 5),
        windowSizeBefore: previous.length,
        windowSizeAfter: values.length,
        headBefore: previous[0] === undefined ? null : previous[0],
        headAfter: values[0] === undefined ? null : values[0]
      });
      while (transitions.length > maxEntries) transitions.shift();
    }
    previous = values;
    return {
      status: "tracking",
      baselineAt,
      windowSize: values.length,
      snapshotCount,
      counters,
      transitions: transitions.slice(),
      note: "Transitions observées entre instantanés DOM; observedAt est l'heure de détection, pas celle de la fin de manche. Rien n'est enregistré."
    };
  };
}

/**
 * Émetteur de transitions : à chaque changement de la liste d'historique, remet à l'arrière-plan les DEUX
 * instantanés complets. L'extension ne décide pas qu'une manche est terminée : le serveur refait l'alignement.
 * Le premier instantané sert de point de départ et n'est jamais envoyé. Une fenêtre vide est ignorée.
 */
function createHistoryTransitionEmitter(send, now = () => new Date().toISOString()) {
  let previous = null;
  return (historyReport) => {
    const container = ((historyReport && historyReport.containers) || [])[0];
    if (!container) return false;
    const values = container.entries.filter((entry) => entry.validMultiplier).map((entry) => entry.value);
    if (values.length < 10) return false;
    if (previous === null) {
      previous = values;
      return false;
    }
    const changed = previous.length !== values.length || previous.some((value, index) => value !== values[index]);
    if (!changed) return false;
    send({ schema: 1, observed_at: now(), previous, next: values });
    previous = values;
    return true;
  };
}

/**
 * Agrégats PUBLICS affichés dans la fenêtre du jeu : « Paris totaux », « x/y » et « Gain total ».
 * Lecture seule et diagnostic : aucune ligne de joueur (noms masqués, mises individuelles) n'est lue,
 * rien n'est envoyé à l'API ni enregistré. Ne prouve pas que ces valeurs aident à prédire quoi que ce soit.
 */
const AGGREGATE_LABEL = /^(paris totaux|gain total(?:\s*mga)?)\s*(.*)$/i;
const MAX_AGGREGATE_SAMPLES = 120;

function normalizeText(value) {
  return String(value || "").replace(/[\u00a0\s]+/g, " ").trim();
}

function parseAggregateNumber(text) {
  const clean = normalizeText(text).replace(/\s*mga$/i, "");
  if (!/^\d{1,3}(?:[ ,]\d{3})*(?:\.\d{1,2})?$|^\d+(?:\.\d{1,2})?$/.test(clean)) return null;
  const value = Number(clean.replace(/[ ,]/g, ""));
  return Number.isFinite(value) ? value : null;
}

function parseAggregateValue(rest) {
  const text = normalizeText(rest);
  const ratio = /^(\d{1,7})\s*\/\s*(\d{1,7})$/.exec(text);
  if (ratio) return { kind: "ratio", placed: Number(ratio[1]), total: Number(ratio[2]) };
  const number = parseAggregateNumber(text);
  return number === null ? null : { kind: "number", value: number };
}

function inspectRoundAggregates(elements) {
  const matches = [];
  for (const node of elements || []) {
    const text = normalizeText(node.textContent);
    if (text.length === 0 || text.length > 48) continue;
    const found = AGGREGATE_LABEL.exec(text);
    if (!found) continue;
    const parsed = parseAggregateValue(found[2]);
    if (!parsed) continue;
    matches.push({ node, label: found[1].toLowerCase().startsWith("paris") ? "paris_totaux" : "gain_total", parsed, rawText: text });
  }
  // Garde l'élément le plus profond : on ignore un ancêtre dont un descendant correspond déjà.
  const items = matches.filter((item) => !matches.some((other) => other !== item && other.label === item.label
    && other.rawText === item.rawText && isAncestor(item.node, other.node)));
  return {
    found: items.length > 0,
    items: items.slice(0, 6).map((item) => ({
      label: item.label,
      rawText: item.rawText,
      kind: item.parsed.kind,
      value: item.parsed.kind === "number" ? item.parsed.value : null,
      placed: item.parsed.kind === "ratio" ? item.parsed.placed : null,
      total: item.parsed.kind === "ratio" ? item.parsed.total : null,
      path: elementPath(item.node)
    })),
    note: "Agrégats publics seulement; les lignes individuelles des joueurs ne sont jamais lues."
  };
}

function isAncestor(candidate, node) {
  for (let current = node && node.parentElement; current; current = current.parentElement) {
    if (current === candidate) return true;
  }
  return false;
}

/** Journal borné : une entrée seulement quand l'une des valeurs affichées change. */
function createAggregateSampler(maxEntries = MAX_AGGREGATE_SAMPLES, now = () => new Date().toISOString()) {
  let last = null;
  let sampleCount = 0;
  const samples = [];
  return (aggregates) => {
    if (!aggregates || !aggregates.found) return { status: "no_aggregates", sampleCount, samples: samples.slice() };
    const current = { totalBets: null, placed: null, totalBetsDenominator: null, totalWin: null };
    for (const item of aggregates.items) {
      if (item.label === "paris_totaux" && item.kind === "number") current.totalBets = item.value;
      if (item.label === "paris_totaux" && item.kind === "ratio") { current.placed = item.placed; current.totalBetsDenominator = item.total; }
      if (item.label === "gain_total" && item.kind === "number") current.totalWin = item.value;
    }
    const changed = last === null || Object.keys(current).some((key) => current[key] !== last[key]);
    if (changed) {
      sampleCount += 1;
      samples.push({ at: now(), ...current });
      while (samples.length > maxEntries) samples.shift();
    }
    last = current;
    return { status: "tracking", sampleCount, samples: samples.slice(), note: "Valeurs affichées échantillonnées; rien n'est enregistré." };
  };
}

function inspectFrameDocument(child) {
  const scan = scanOpenDocument(child, MAX_INSPECTED_ELEMENTS);
  const history = inspectMultiplierHistories(child, {}, scan.elements, scan.truncated);
  return {
    actuallyInspected: true,
    inspectionStatus: "inspected_from_parent_same_origin",
    documentAccessible: true,
    inspectedElementCount: scan.inspectedElementCount,
    truncated: scan.truncated,
    openShadowRootCount: scan.shadowRoots.length,
    selector: "._items_7l84e_35",
    selectorExists: history.foundContainerCount > 0,
    matchCount: history.foundContainerCount,
    numericTextCount: history.numericTextCount
  };
}

function scanDocument(doc, context, mainScan = null) {
  const framesInScannedDom = (mainScan || scanOpenDocument(doc, MAX_INSPECTED_ELEMENTS)).elements
    .filter((element) => String(element.tagName || "").toLowerCase() === "iframe");
  const frames = framesInScannedDom.slice(0, 20).map((frame, index) => {
    let accessible = false;
    let contentType = null;
    let sameOrigin = null;
    let inaccessibleReason = null;
    let matchesAllowedHost = null;
    let origin = null;
    let originSource = "unresolved";
    let loadState = "unknown";
    let child = null;
    let inspection = {
      actuallyInspected: false,
      inspectionStatus: "identified_only",
      documentAccessible: false,
      selector: "._items_7l84e_35",
      selectorExists: null,
      matchCount: null,
      numericTextCount: null
    };
    try {
      const rawSrc = frame.getAttribute("src");
      const frameUrl = new URL(rawSrc || "about:blank", doc.location.href);
      if (rawSrc) {
        sameOrigin = frameUrl.origin === new URL(doc.location.href).origin;
        matchesAllowedHost = frameUrl.protocol === "https:" && isAllowedHost(frameUrl.hostname);
      }
      origin = !rawSrc || frameUrl.origin === "null" ? null : frameUrl.origin;
      originSource = rawSrc ? "iframe_src_origin_only" : "src_absent_origin_inherited_or_unknown";
      child = frame.contentDocument;
      accessible = Boolean(child && child.documentElement);
      if (accessible) {
        const childOrigin = child.location && child.location.origin;
        if (childOrigin && childOrigin !== "null") {
          origin = childOrigin;
          originSource = "accessible_child_document";
        }
        sameOrigin = childOrigin === doc.location.origin;
        if (childOrigin) {
          const childUrl = new URL(childOrigin);
          matchesAllowedHost = childUrl.protocol === "https:" && isAllowedHost(childUrl.hostname);
        }
        contentType = child.contentType || null;
        loadState = child.readyState || "ready_state_unavailable";
        inspection = inspectFrameDocument(child);
      } else {
        inaccessibleReason = sameOrigin === true
          ? "Document absent, vide ou pas encore chargé (contentDocument nul)."
          : sameOrigin === false
            ? "Document non accessible depuis cette page; politique de même origine."
            : "Document enfant absent ou origine inconnue; état non déterminable depuis le parent.";
        inspection.inspectionStatus = matchesAllowedHost === true
          ? "identified_only_child_report_may_be_available"
          : matchesAllowedHost === false
            ? "identified_only_not_authorized_for_injection"
            : "identified_only_origin_unknown";
        loadState = "unreadable_or_not_loaded_child_state_unavailable";
      }
    } catch (_error) {
      inaccessibleReason = "Accès refusé par le navigateur; politique de même origine.";
      inspection.inspectionStatus = matchesAllowedHost === true
        ? "identified_only_child_report_may_be_available"
        : matchesAllowedHost === false
          ? "identified_only_not_authorized_for_injection"
          : "identified_only_origin_unknown";
      loadState = "unreadable_child_state_unavailable";
    }
    let title = safeAttribute(frame, "title", /^[\p{L}\p{N} _.-]{1,60}$/u);
    return {
      index: index + 1,
      srcAttributeAccessible: true,
      srcPresent: Boolean(frame.getAttribute("src")),
      origin,
      originSource,
      loadState,
      accessible,
      documentAccessible: accessible,
      sameOrigin,
      matchesAllowedHost,
      contentScriptExpectedByManifest: matchesAllowedHost,
      contentScriptStatus: matchesAllowedHost === true
        ? "eligible_by_manifest_report_not_confirmed_here"
        : matchesAllowedHost === false
          ? "outside_manifest_matches_no_injection_expected"
          : "origin_unknown_manifest_match_undetermined",
      contentType,
      title,
      ...inspection,
      inaccessibleReason
    };
  });
  const inspected = mainScan || scanOpenDocument(doc, MAX_INSPECTED_ELEMENTS);
  const history = inspectMultiplierHistories(doc, {}, inspected.elements, inspected.truncated);
  return {
    context,
    actuallyInspected: true,
    documentAccessible: true,
    contentType: doc.contentType || null,
    domElementCount: countElements(doc),
    inspectedElementCount: inspected.inspectedElementCount,
    inspectionTruncated: inspected.truncated,
    openShadowRootCount: inspected.shadowRoots.length,
    selector: "._items_7l84e_35",
    selectorExists: history.foundContainerCount > 0,
    selectorMatchCount: history.foundContainerCount,
    numericTextCount: history.numericTextCount,
    canvasCount: doc.querySelectorAll("canvas").length,
    svgCount: doc.querySelectorAll("svg").length,
    iframeCount: frames.length,
    iframes: frames
  };
}

function collectDiagnostic(doc, limits = {}, context = {}) {
  const domScan = scanOpenDocument(doc, limits.maxInspectedElements || MAX_INSPECTED_ELEMENTS);
  const page = scanDocument(doc, context.isTopFrame === false ? "document d'une frame Bet261 ciblée" : "document courant", domScan);
  const aviator = inspectAviatorComponents(doc, limits, domScan.elements);
  const multiplierHistories = inspectMultiplierHistories(doc, limits, domScan.elements, domScan.truncated);
  const roundAggregates = inspectRoundAggregates(domScan.elements);
  const readyState = doc.readyState || "unknown";
  // Keep observer node references private to the runtime; JSON output contains only safe summaries.
  const report = {
    diagnostic: "aviator_component_local_read_only",
    extractionEnabled: false,
    completionSemantics: "unknown; aucune valeur numérique n'est considérée comme une manche terminée",
    generatedAt: new Date().toISOString(),
    documentContext: {
      origin: doc.location && doc.location.origin ? doc.location.origin : null,
      frame: context.isTopFrame === false ? "embedded_frame" : context.isTopFrame === true ? "top_frame" : "unknown",
      contentScriptActive: context.contentScriptActive === true,
      contentScriptEvidence: context.contentScriptActive === true
        ? "ce rapport est produit par le content script exécuté dans ce document"
        : "non vérifiable hors du runtime Firefox",
      readyState,
      documentAccessible: true,
      actuallyInspected: true,
      documentCompleteAtInspection: readyState === "complete",
      inspectionNumber: context.inspectionNumber || 1,
      firstInspection: context.firstInspection !== false,
      contentScriptRunAt: "document_idle",
      gameDomEvidence: aviator.found && multiplierHistories.foundContainerCount
        ? "component_and_history_found"
        : aviator.found ? "component_only"
          : multiplierHistories.foundContainerCount ? "history_only" : "neither_found"
    },
    domScan: {
      inspectedElementCount: domScan.inspectedElementCount,
      elementLimit: limits.maxInspectedElements || MAX_INSPECTED_ELEMENTS,
      truncated: domScan.truncated,
      openShadowRootCount: domScan.shadowRoots.length
    },
    documents: [page],
    aviatorComponents: {
      found: aviator.found,
      componentCount: aviator.componentCount,
      components: aviator.components,
      limits: aviator.found ? [] : ["Le composant hg-aviator-llc-game avec la classe observée n'est pas accessible dans ce document."]
    },
    multiplierHistories,
    roundAggregates,
    inaccessibleFrameCount: page.iframes.filter((frame) => !frame.accessible).length,
    inaccessibleFrameReasons: page.iframes.filter((frame) => !frame.accessible).map(({ index, inaccessibleReason }) => ({ index, reason: inaccessibleReason }))
  };
  const observationRoots = [{ node: doc.documentElement, context: "document accessible" }, ...domScan.shadowRoots.map((node) => ({ node, context: "Shadow DOM ouvert accessible" }))];
  Object.defineProperty(report, "_observationRoots", { value: observationRoots, enumerable: false });
  return report;
}

function createCandidateChangeTracker() {
  let previous = null;
  let changes = 0;
  let lastChangedAt = null;
  return (report) => {
    const current = (report.aviatorComponents?.components || []).flatMap((component) =>
      component.numericCandidates.map((candidate) => ({ ...candidate, componentIndex: component.index }))
    );
    const signature = (item) => JSON.stringify([item.value, item.category, item.componentIndex, item.path]);
    const before = new Set((previous || []).map(signature));
    const after = new Set(current.map(signature));
    const added = previous === null ? [] : current.filter((item) => !before.has(signature(item)));
    const removed = (previous || []).filter((item) => !after.has(signature(item)));
    const changed = previous !== null && (added.length > 0 || removed.length > 0);
    if (changed) { changes += 1; lastChangedAt = new Date().toISOString(); }
    previous = current;
    return { count: changes, changedSincePreviousScan: changed, lastChangedAt, added: added.slice(0, 10), removed: removed.slice(0, 10) };
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { collectDiagnostic, createCandidateChangeTracker, alignHistoryWindow, createHistoryEvolutionTracker, createHistoryTransitionLog, createHistoryTransitionEmitter, createAggregateSampler, inspectRoundAggregates, parseAggregateNumber, elementPath, inspectAviatorComponents, inspectMultiplierHistories, inspectPersonalBets, isAllowedHost, isValidDisplayedMultiplier, scanDocument };
}

if (typeof browser !== "undefined" && typeof document !== "undefined") {
  const isTopFrame = window.top === window;
  let diagnosticHost = null;
  let state = null;
  let connectionState = null;
  let button = null;
  let reportBox = null;
  if (isTopFrame) {
    diagnosticHost = document.createElement("div");
    diagnosticHost.id = "ail-local-diagnostic";
    diagnosticHost.style.cssText = "position:fixed;z-index:2147483647;right:8px;bottom:8px";
    const shadow = diagnosticHost.attachShadow({ mode: "closed" });
    const panel = document.createElement("section");
    panel.style.cssText = "font:12px sans-serif;background:#17212b;color:white;padding:8px 10px;border-radius:6px;max-width:360px";
    state = document.createElement("div");
    state.textContent = "Aviator Lab : lecture locale des paris affichés; extraction des manches désactivée.";
    connectionState = document.createElement("div");
    connectionState.textContent = "API locale : vérification en cours.";
    button = document.createElement("button");
    button.type = "button";
    button.textContent = "Actualiser le diagnostic";
    reportBox = document.createElement("textarea");
    reportBox.readOnly = true;
    reportBox.rows = 10;
    reportBox.style.cssText = "box-sizing:border-box;width:100%;margin-top:6px;font:11px monospace;color:#111";
    reportBox.setAttribute("aria-label", "Rapport technique local à copier manuellement");
    const copyHelp = document.createElement("div");
    copyHelp.textContent = "Rapport technique local. Les changements de l'historique sont envoyés à l'API locale (127.0.0.1) pour enregistrement.";
    panel.append(state, connectionState, button, reportBox, copyHelp);
    shadow.appendChild(panel);
    (document.documentElement || document.body).appendChild(diagnosticHost);
  }

  let mutationCount = 0;
  let inspectionNumber = 0;
  const trackCandidateChanges = createCandidateChangeTracker();
  const trackHistoryEvolution = createHistoryEvolutionTracker();
  const logHistoryTransitions = createHistoryTransitionLog();
  const sampleAggregates = createAggregateSampler();
  const emitHistoryTransition = createHistoryTransitionEmitter((transition) => {
    browser.runtime.sendMessage({ type: "ail-history-transition", transition }).catch(() => {});
  });
  const emitPersonalBets = (bets) => {
    if (bets.length) browser.runtime.sendMessage({ type: "ail-personal-bets", bets: bets.slice(0, 100) }).catch(() => {});
  };
  const recentMutationTargets = [];
  const observedRoots = new WeakSet();
  let scheduledScan = null;
  let lastScanAt = 0;
  const refresh = (force = false) => {
    if (scheduledScan !== null) {
      window.clearTimeout(scheduledScan);
      scheduledScan = null;
    }
    const wait = 1000 - (Date.now() - lastScanAt);
    if (!force && lastScanAt && wait > 0) {
      scheduledScan = window.setTimeout(() => {
        scheduledScan = null;
        refresh();
      }, wait);
      return;
    }
    lastScanAt = Date.now();
    inspectionNumber += 1;
    const report = collectDiagnostic(document, {}, {
      isTopFrame,
      contentScriptActive: true,
      inspectionNumber,
      firstInspection: inspectionNumber === 1
    });
    report.domChangeCount = mutationCount;
    report.recentMutationTargets = recentMutationTargets.slice(-20).map(({ key, ...item }) => item);
    report.multiplierCandidateChanges = trackCandidateChanges(report);
    report.historyEvolution = trackHistoryEvolution(report.multiplierHistories);
    report.historyTransitions = logHistoryTransitions(report.multiplierHistories);
    emitHistoryTransition(report.multiplierHistories);
    emitPersonalBets(inspectPersonalBets(document));
    report.aggregateSamples = sampleAggregates(report.roundAggregates);
    report.resultFinality = "indéterminée : une mutation ou une valeur numérique ne confirme pas la fin d'une manche";
    if (isTopFrame) {
      const reportNumber = inspectionNumber;
      reportBox.value = JSON.stringify(report, null, 2);
      state.textContent = `DOM inspecté localement; ${inspectPersonalBets(document).length} pari(s) affiché(s) détecté(s).`;
      browser.runtime.sendMessage({ type: "ail-get-frame-diagnostics" }).then((frameReports) => {
        if (reportNumber !== inspectionNumber) return;
        const reports = Array.isArray(frameReports) ? frameReports : [];
        report.embeddedFrameDiagnostics = reports;
        for (const frame of report.documents[0].iframes) {
          const sameOriginReports = frame.origin
            ? reports.filter((item) => item.documentContext?.origin === frame.origin)
            : [];
          frame.contentScriptStatus = sameOriginReports.length
            ? `report_received_for_origin_${sameOriginReports.length}_frame(s)_mapping_ambiguous`
            : frame.contentScriptExpectedByManifest === true
              ? "eligible_by_manifest_no_child_report_received"
              : frame.contentScriptExpectedByManifest === false
                ? "outside_manifest_matches_no_injection_expected"
                : "origin_unknown_manifest_match_undetermined";
        }
        reportBox.value = JSON.stringify(report, null, 2);
      }).catch(() => {});
    } else {
      browser.runtime.sendMessage({
        type: "ail-frame-diagnostic",
        report: JSON.parse(JSON.stringify(report))
      }).catch(() => {});
    }
    for (const { node, context } of report._observationRoots || []) {
      if (!node || observedRoots.has(node)) continue;
      observedRoots.add(node);
      new MutationObserver((records) => {
        const relevantRecords = records.filter((record) => {
          let target = record.target && record.target.nodeType === 3 ? record.target.parentElement : record.target;
          while (target) {
            if (target === diagnosticHost) return false;
            target = target.parentElement;
          }
          const changedNodes = [...(record.addedNodes || []), ...(record.removedNodes || [])];
          return !changedNodes.some((changed) => changed === diagnosticHost || (changed.contains && diagnosticHost && changed.contains(diagnosticHost)));
        });
        mutationCount += relevantRecords.length;
        for (const record of relevantRecords.slice(-40)) {
          const target = record.target && record.target.nodeType === 3 ? record.target.parentElement : record.target;
          const summary = { type: record.type, context, path: elementPath(target), lastSeenAt: new Date().toISOString() };
          const key = JSON.stringify([summary.context, summary.type, summary.path]);
          const existing = recentMutationTargets.find((item) => item.key === key);
          if (existing) { existing.count += 1; existing.lastSeenAt = summary.lastSeenAt; }
          else recentMutationTargets.push({ ...summary, key, count: 1 });
          while (recentMutationTargets.length > 20) recentMutationTargets.shift();
        }
        if (scheduledScan === null) {
          scheduledScan = window.setTimeout(() => {
            scheduledScan = null;
            refresh();
          }, 1000);
        }
      }).observe(node, { subtree: true, childList: true, characterData: true, attributes: true });
    }
  };
  if (button) button.addEventListener("click", () => refresh(true));
  refresh(true);
  window.setInterval(refresh, 5000);
  const sendHeartbeat = () => {
    if (!isTopFrame) return;
    browser.runtime.sendMessage({ type: "ail-heartbeat" }).then((connected) => {
      connectionState.textContent = connected ? "API locale connectée; enregistrement des manches actif." : "API locale hors ligne; nouvel essai périodique prévu.";
    }).catch(() => { connectionState.textContent = "API locale hors ligne; nouvel essai périodique prévu."; });
  };
  window.setInterval(sendHeartbeat, 30_000);
  sendHeartbeat();
}
