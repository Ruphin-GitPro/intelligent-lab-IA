const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { alignHistoryWindow, collectDiagnostic, createCandidateChangeTracker, createHistoryEvolutionTracker, createHistoryTransitionLog, inspectMultiplierHistories, isAllowedHost } = require("../content.js");
const { API, createFrameDiagnosticStore, postHeartbeat } = require("../background.js");

test("Firefox extension stays narrowly scoped and avoids cookies/network hooks", () => {
  const root = path.resolve(__dirname, "..");
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, []);
  assert.ok(manifest.host_permissions.includes("http://127.0.0.1:8765/*"));
  assert.equal(manifest.content_scripts[0].run_at, "document_idle");
  assert.equal(manifest.content_scripts[0].all_frames, true);
  const source = fs.readFileSync(path.join(root, "content.js"), "utf8");
  assert.doesNotMatch(source, /document\.cookie|webRequest|fetch\s*\(/);
  assert.match(source, /canvasCount/);
});

test("periodic heartbeat reports API interruption and later recovery", async () => {
  const calls = [];
  const unavailable = await postHeartbeat(async (url, options) => {
    calls.push({ url, options });
    throw new Error("offline");
  });
  const recovered = await postHeartbeat(async (url, options) => {
    calls.push({ url, options });
    return { ok: true };
  });

  assert.equal(unavailable, false);
  assert.equal(recovered, true);
  assert.equal(calls.length, 2);
  assert.ok(calls.every((call) => call.url === `${API}/api/v1/heartbeat`));
});

test("embedded-frame diagnostics stay local, bounded, and expire", () => {
  const store = createFrameDiagnosticStore(2, 1000);
  store.put("tab-1:1", { documentContext: { frame: "embedded_frame", origin: "https://bet261.mg" } }, 1000);
  store.put("tab-1:2", { documentContext: { frame: "top_frame" } }, 1000);
  assert.equal(store.list(1500).length, 1);
  assert.equal(store.list(1500)[0].documentContext.frame, "embedded_frame");
  store.put("tab-2:1", { documentContext: { frame: "embedded_frame", origin: "https://game.bet261.mg" } }, 1600);
  store.put("tab-3:1", { documentContext: { frame: "embedded_frame", origin: "https://bet261.mg" } }, 1700);
  assert.equal(store.list(1800).length, 2);
  assert.equal(store.list(3000).length, 0);
});

function fakeDocument({ iframes = [], components = [], extraNodes = [] } = {}) {
  const body = {};
  const documentElement = element("html", { children: [...components, ...extraNodes, ...iframes] });
  return {
    body,
    documentElement,
    readyState: "complete",
    contentType: "text/html",
    location: { href: "https://bet261.mg/game", origin: "https://bet261.mg" },
    querySelectorAll(selector) {
      if (selector === "iframe") return iframes;
      if (selector === "hg-aviator-llc-game") return components;
      if (selector === "canvas" || selector === "svg") return [];
      return [];
    },
    getElementsByTagName: () => ({ length: 5 })
  };
}

function element(tagName, { classes = "", text = "", children = [], shadowRoot = null, role = null, type = null, attributes = [] } = {}) {
  const node = {
    nodeType: 1, tagName: tagName.toUpperCase(), className: classes, textContent: text, children, parentElement: null,
    shadowRoot, attributes,
    getAttribute(name) { return name === "role" ? role : name === "type" ? type : attributes.find((item) => item.name === name)?.value || null; },
    getRootNode() { return { host: null }; }
  };
  children.forEach((child) => { child.parentElement = node; });
  if (shadowRoot) shadowRoot.host = node;
  return node;
}

function shadowRoot(children) {
  const root = { nodeType: 11, children, host: null };
  children.forEach((child) => { child.parentElement = null; child.getRootNode = () => root; });
  return root;
}

test("diagnostic recursively inspects only the Aviator host and open shadow roots", () => {
  const candidate = element("span", { classes: "round-result", text: "47" });
  const marked = element("span", { classes: "multiplier", text: "2.35x" });
  const svg = element("svg");
  const nestedRoot = shadowRoot([marked, svg]);
  const nestedComponent = element("game-history", { shadowRoot: nestedRoot });
  const root = shadowRoot([candidate, nestedComponent]);
  const host = element("hg-aviator-llc-game", { classes: "aviator-llc-game", shadowRoot: root });
  const report = collectDiagnostic(fakeDocument({ components: [host] }));

  assert.equal(report.extractionEnabled, false);
  assert.match(report.completionSemantics, /unknown/);
  assert.equal(report.aviatorComponents.found, true);
  assert.equal(report.aviatorComponents.components[0].shadowRoot, "open");
  assert.equal(report.aviatorComponents.components[0].openShadowRootCount, 2);
  assert.equal(report.aviatorComponents.components[0].svgCount, 1);
  assert.deepEqual(report.aviatorComponents.components[0].numericCandidates.map((item) => item.value), ["47", "2.35x"]);
  assert.equal(report.aviatorComponents.components[0].numericCandidates[0].category, "number_without_multiplier_marker");
  assert.equal(report.aviatorComponents.components[0].numericCandidates[1].category, "number_with_x_or_times_marker");
  assert.equal(report.aviatorComponents.components[0].numericCandidates[1].path[0].classes[0], "multiplier");
  assert.equal(JSON.stringify(report).includes("Balance"), false);
});

test("targeted DOM diagnostic parses decimal candidates from observed container buttons", () => {
  const first = element("button", {
    classes: "_container_12jzl_1 _purple_12jzl_28", text: "3.55",
    children: [element("span", { classes: "_container_1p5jb_1 _medium_1p5jb_14 _size-12_1p5jb_26", text: "3.55" })]
  });
  const second = element("button", {
    classes: "_container_12jzl_1 _blue_12jzl_24", text: "1.36",
    children: [element("span", { classes: "_container_1p5jb_1 _medium_1p5jb_14 _size-12_1p5jb_26", text: "1.36" })]
  });
  const invalid = element("button", { text: "0.4" });
  const list = element("div", { classes: "_items_7l84e_35 _animate_7l84e_46", children: [first, second, invalid] });
  const host = element("hg-aviator-llc-game", { classes: "aviator-llc-game" });
  const doc = fakeDocument({ components: [host], extraNodes: [list] });
  const report = inspectMultiplierHistories(doc);
  const integratedReport = collectDiagnostic(doc, {}, { isTopFrame: false, inspectionNumber: 3, firstInspection: false });
  assert.equal(integratedReport.extractionEnabled, false);
  assert.deepEqual(integratedReport.multiplierHistories.containers[0].entries.map((item) => item.value), ["3.55", "1.36", null]);
  assert.equal(integratedReport.documentContext.frame, "embedded_frame");
  assert.equal(integratedReport.documentContext.readyState, "complete");
  assert.equal(integratedReport.documentContext.firstInspection, false);
  assert.equal(integratedReport.documentContext.gameDomEvidence, "component_and_history_found");
  assert.equal(report.foundContainerCount, 1);
  assert.equal(report.containers[0].buttonCount, 3);
  assert.equal(report.containers[0].componentIndex, null);
  assert.deepEqual(report.containers[0].entries.map((item) => item.value), ["3.55", "1.36", null]);
  assert.deepEqual(report.containers[0].entries.map((item) => item.validMultiplier), [true, true, false]);
  assert.equal(report.containers[0].invalidValueCount, 1);
  assert.equal(report.containers[0].entries[0].domOrder, 1);
  assert.deepEqual(report.containers[0].possibleDomIdentifiers, []);
  assert.deepEqual(report.containers[0].entries[0].possibleDomIdentifiers, []);
});

test("history selector is searched independently and can appear after the initial inspection", () => {
  const doc = fakeDocument();
  const initial = collectDiagnostic(doc, {}, { isTopFrame: true, inspectionNumber: 1, firstInspection: true });
  assert.equal(initial.aviatorComponents.found, false);
  assert.equal(initial.multiplierHistories.foundContainerCount, 0);
  assert.equal(initial.documentContext.gameDomEvidence, "neither_found");

  const button = element("button", { text: "3.55" });
  doc.documentElement.children.push(element("div", { classes: "_items_7l84e_35", children: [button] }));
  const afterLazyLoad = collectDiagnostic(doc, {}, { isTopFrame: true, inspectionNumber: 2, firstInspection: false });
  assert.equal(afterLazyLoad.aviatorComponents.found, false);
  assert.equal(afterLazyLoad.multiplierHistories.foundContainerCount, 1);
  assert.equal(afterLazyLoad.documentContext.gameDomEvidence, "history_only");
});

test("history evolution infers only visible list direction and starts from a baseline", () => {
  const track = createHistoryEvolutionTracker();
  const snapshot = (values) => ({ containers: [{ componentIndex: 1, entries: values.map((value) => ({ value, validMultiplier: true, possibleDomIdentifiers: [] })) }] });
  assert.equal(track(snapshot(["3.55", "1.36"])).direction, "indéterminé");
  assert.equal(track(snapshot(["4.10", "3.55", "1.36"])).direction, "début");
  assert.equal(track(snapshot(["3.55", "1.36", "2.08"])).direction, "fin-avec-retrait-au-début");
  const quiet = track(snapshot(["3.55", "1.36", "2.08"]));
  assert.equal(quiet.changedSincePreviousScan, false);
  assert.equal(quiet.direction, "aucun-changement");
  assert.equal(quiet.lastChange.direction, "fin-avec-retrait-au-début");
});

test("equal repeated values do not establish a unique insertion side", () => {
  const track = createHistoryEvolutionTracker();
  const snapshot = (values) => ({ containers: [{ componentIndex: 1, entries: values.map((value) => ({ value, validMultiplier: true, possibleDomIdentifiers: [] })) }] });
  track(snapshot(["2.00", "2.00"]));
  assert.equal(track(snapshot(["2.00", "2.00", "2.00"])).direction, "ambiguë");
});

test("diagnostic separates inspected same-origin iframe from an identified-only cross-origin iframe", () => {
  const history = element("div", { classes: "_items_7l84e_35", children: [element("button", { text: "3.55" })] });
  const accessible = fakeDocument({ extraNodes: [history] });
  const sameOriginFrame = element("iframe");
  sameOriginFrame.getAttribute = (name) => name === "src" ? "/game-frame" : null;
  sameOriginFrame.contentDocument = accessible;
  const crossOriginFrame = element("iframe");
  crossOriginFrame.getAttribute = (name) => name === "src" ? "https://game-provider.example/iframe" : null;
  Object.defineProperty(crossOriginFrame, "contentDocument", { get() { throw new Error("cross-origin"); } });
  const report = collectDiagnostic(fakeDocument({
    iframes: [sameOriginFrame, crossOriginFrame]
  }));

  assert.equal(report.documents[0].iframes[0].index, 1);
  assert.equal(report.documents[0].iframes[0].accessible, true);
  assert.equal(report.documents[0].iframes[0].contentType, "text/html");
  assert.equal(report.documents[0].iframes[0].actuallyInspected, true);
  assert.equal(report.documents[0].iframes[0].documentAccessible, true);
  assert.equal(report.documents[0].iframes[0].selectorExists, true);
  assert.equal(report.documents[0].iframes[0].matchCount, 1);
  assert.equal(report.documents[0].iframes[0].numericTextCount, 1);
  assert.equal(report.documents[0].iframes[0].matchesAllowedHost, true);
  assert.equal(report.documents[0].iframes[0].origin, "https://bet261.mg");
  assert.equal(report.documents[0].iframes[0].originSource, "accessible_child_document");
  assert.equal(report.documents[0].iframes[0].loadState, "complete");
  assert.equal(report.documents[0].iframes[0].contentScriptExpectedByManifest, true);
  assert.equal(report.documents[0].iframes[1].index, 2);
  assert.equal(report.documents[0].iframes[1].accessible, false);
  assert.equal(report.documents[0].iframes[1].origin, "https://game-provider.example");
  assert.equal(report.documents[0].iframes[1].originSource, "iframe_src_origin_only");
  assert.equal(report.documents[0].iframes[1].loadState, "unreadable_child_state_unavailable");
  assert.equal(report.documents[0].iframes[1].contentScriptExpectedByManifest, false);
  assert.equal(report.documents[0].iframes[1].actuallyInspected, false);
  assert.equal(report.documents[0].iframes[1].selectorExists, null);
  assert.equal(report.documents[0].iframes[1].matchCount, null);
  assert.equal(report.documents[0].iframes[1].inspectionStatus, "identified_only_not_authorized_for_injection");
  assert.equal(report.inaccessibleFrameCount, 1);
  assert.match(report.inaccessibleFrameReasons[0].reason, /même origine/);
  assert.equal(report.inaccessibleFrameReasons[0].index, 2);
  assert.equal(report.documents[0].iframes[1].origin, "https://game-provider.example");
  assert.equal(JSON.stringify(report).includes("/iframe"), false);
});

test("top document selector status is explicit even when the custom game host is absent", () => {
  const list = element("div", { classes: "_items_7l84e_35", children: [element("button", { text: "1.36" })] });
  const report = collectDiagnostic(fakeDocument({ extraNodes: [list] }), {}, { isTopFrame: true });
  assert.equal(report.documents[0].actuallyInspected, true);
  assert.equal(report.documents[0].documentAccessible, true);
  assert.equal(report.documents[0].selectorExists, true);
  assert.equal(report.documents[0].selectorMatchCount, 1);
  assert.equal(report.documents[0].numericTextCount, 1);
  assert.equal(report.aviatorComponents.found, false);
  assert.equal(report.extractionEnabled, false);
});

test("component candidate history tracks changes but does not label them completed rounds", () => {
  const track = createCandidateChangeTracker();
  const snapshot = (values) => ({ aviatorComponents: { components: [{ index: 1,
    numericCandidates: values.map((value) => ({ value, category: "number_with_x_or_times_marker", path: [] }))
  }] } });

  assert.deepEqual(track(snapshot(["2.10x"])), {
    count: 0, changedSincePreviousScan: false, lastChangedAt: null, added: [], removed: []
  }); // Existing history is a baseline, not a new result.
  assert.equal(track(snapshot(["2.10x"])).count, 0);
  const added = track(snapshot(["2.10x", "3.40x"]));
  assert.equal(added.count, 1);
  assert.equal(added.changedSincePreviousScan, true);
  assert.equal(added.added[0].value, "3.40x");
  assert.ok(added.lastChangedAt);
  assert.equal(track(snapshot(["3.40x"])).count, 2);
});

test("closed or absent host shadow roots are explicitly reported as unavailable", () => {
  const host = element("hg-aviator-llc-game", { classes: "aviator-llc-game" });
  const report = collectDiagnostic(fakeDocument({ components: [host] }));
  assert.equal(report.aviatorComponents.components[0].shadowRoot, "not_exposed_absent_or_closed");
  assert.match(report.aviatorComponents.components[0].limits[0], /absent et fermé/);
  assert.equal(report._observationRoots.length, 1);
});

test("manifest covers exactly the Bet261 and game-frame origins, and no broader HTTPS wildcard", () => {
  const root = path.resolve(__dirname, "..");
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8"));
  const httpsHosts = manifest.host_permissions.filter((pattern) => pattern.startsWith("https://"));
  const gamePattern = "https://*.crash.aviator.studio/*";
  assert.ok(httpsHosts.includes(gamePattern));
  assert.deepEqual(manifest.content_scripts[0].matches.slice().sort(), httpsHosts.slice().sort());
  assert.ok(!httpsHosts.some((pattern) => /^https:\/\/(\*|\*\.[a-z]+)\//.test(pattern)));
  assert.deepEqual(manifest.permissions, []);
});

test("isAllowedHost accepts only Bet261 and the game-frame domain with real subdomain boundaries", () => {
  assert.equal(isAllowedHost("bet261.mg"), true);
  assert.equal(isAllowedHost("www.bet261.mg"), true);
  assert.equal(isAllowedHost("crash.aviator.studio"), true);
  assert.equal(isAllowedHost("af4.crash.aviator.studio"), true);
  assert.equal(isAllowedHost("AF4.Crash.Aviator.Studio"), true);
  assert.equal(isAllowedHost("aviator.studio"), false);
  assert.equal(isAllowedHost("evilcrash.aviator.studio"), false);
  assert.equal(isAllowedHost("crash.aviator.studio.evil.example"), false);
  assert.equal(isAllowedHost(""), false);
  assert.equal(isAllowedHost(null), false);
});

// Données SYNTHÉTIQUES : valeurs inventées pour tester l'algorithme, sans rapport avec le jeu réel.
const SYNTHETIC_WINDOW = ["1.34", "2.20", "24.34", "1.35", "1.04", "1.05", "1.19", "2.87", "1.00", "1.10", "7.53", "1.51"];

test("alignHistoryWindow detects one value inserted at the start and one dropped at the end", () => {
  const next = ["3.33", ...SYNTHETIC_WINDOW.slice(0, -1)];
  const result = alignHistoryWindow(SYNTHETIC_WINDOW, next);
  assert.equal(result.kind, "shift");
  assert.equal(result.shift, 1);
  assert.deepEqual(result.addedValues, ["3.33"]);
});

test("alignHistoryWindow detects two insertions between scans, newest first", () => {
  const next = ["4.44", "3.33", ...SYNTHETIC_WINDOW.slice(0, -2)];
  const result = alignHistoryWindow(SYNTHETIC_WINDOW, next);
  assert.equal(result.kind, "shift");
  assert.equal(result.shift, 2);
  assert.deepEqual(result.addedValues, ["4.44", "3.33"]);
});

test("alignHistoryWindow reports no change for identical lists", () => {
  assert.equal(alignHistoryWindow(SYNTHETIC_WINDOW, SYNTHETIC_WINDOW.slice()).kind, "no_change");
});

test("alignHistoryWindow refuses to align when an old value was modified or the list was replaced", () => {
  const modified = SYNTHETIC_WINDOW.slice();
  modified[5] = "9.99";
  assert.equal(alignHistoryWindow(SYNTHETIC_WINDOW, ["3.33", ...modified.slice(0, -1)]).kind, "unalignable");
  const replaced = ["5.01", "5.02", "5.03", "5.04", "5.05", "5.06", "5.07", "5.08", "5.09", "5.10", "5.11", "5.12"];
  assert.equal(alignHistoryWindow(SYNTHETIC_WINDOW, replaced).kind, "unalignable");
});

test("alignHistoryWindow flags repeated patterns as ambiguous instead of guessing", () => {
  const periodic = Array.from({ length: 30 }, (_, index) => (index % 2 === 0 ? "2.00" : "1.00")); // fenêtre de 30, comme dans le jeu observé
  const next = ["1.00", ...periodic.slice(0, -1)];
  const result = alignHistoryWindow(periodic, next);
  assert.equal(result.kind, "ambiguous");
  assert.ok(result.candidateShifts.length > 1);
  assert.deepEqual(result.addedValues, []);
});

test("alignHistoryWindow keeps repeated equal values apart from real insertions when the window differs", () => {
  const withRepeats = ["1.00", "1.00", "1.00", "2.50", "1.20", "1.30", "1.40", "1.50", "1.60", "1.70", "1.80", "1.90"];
  const next = ["1.00", ...withRepeats.slice(0, -1)];
  const result = alignHistoryWindow(withRepeats, next);
  assert.equal(result.kind, "shift");
  assert.equal(result.shift, 1);
  assert.deepEqual(result.addedValues, ["1.00"]);
});

test("createHistoryTransitionLog records a baseline, then shifts, and never labels them completed rounds", () => {
  let tick = 0;
  const log = createHistoryTransitionLog(3, () => `t${(tick += 1)}`);
  const asReport = (values) => ({ containers: [{ entries: values.map((value) => ({ value, validMultiplier: true })) }] });
  let window = SYNTHETIC_WINDOW.slice();

  const first = log(asReport(window));
  assert.equal(first.status, "baseline_recorded");
  assert.equal(first.transitions.length, 0);

  const steps = ["3.01", "3.02", "3.03", "3.04"];
  let last;
  for (const value of steps) {
    window = [value, ...window.slice(0, -1)];
    last = log(asReport(window));
  }
  assert.equal(last.status, "tracking");
  assert.equal(last.counters.shift, 4);
  assert.equal(last.counters.shiftOfOne, 4);
  assert.equal(last.transitions.length, 3);
  assert.deepEqual(last.transitions.map((item) => item.addedValues[0]), ["3.02", "3.03", "3.04"]);
  assert.ok(!JSON.stringify(last).includes("completed"));
  assert.match(last.note, /pas celle de la fin de manche/);

  const unchanged = log(asReport(window));
  assert.equal(unchanged.counters.shift, 4);

  const none = log({ containers: [] });
  assert.equal(none.status, "no_history_container");
});


// --- Émetteur de transitions et file d'envoi (valeurs SYNTHÉTIQUES) ---
const { createHistoryTransitionEmitter } = require("../content.js");
const { createTransitionQueue } = require("../background.js");

function historyOf(values) {
  return { containers: [{ entries: values.map((value) => ({ value, validMultiplier: true })) }] };
}
const synthetic = (start) => Array.from({ length: 30 }, (_, i) => (1 + ((start + i) * 37 % 997) / 100).toFixed(2));

test("émetteur : baseline non envoyée, puis une transition envoyée avec les deux instantanés", () => {
  const sent = [];
  const emit = createHistoryTransitionEmitter((t) => sent.push(t), () => "2026-10-18T10:00:00Z");
  const w1 = synthetic(1);
  assert.equal(emit(historyOf(w1)), false);
  assert.equal(emit(historyOf(w1)), false);
  const w0 = [synthetic(0)[0], ...w1.slice(0, -1)];
  assert.equal(emit(historyOf(w0)), true);
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0].previous, w1);
  assert.deepEqual(sent[0].next, w0);
  assert.equal(sent[0].schema, 1);
});

test("émetteur : fenêtre trop courte ou absente ignorée", () => {
  const sent = [];
  const emit = createHistoryTransitionEmitter((t) => sent.push(t));
  assert.equal(emit({ containers: [] }), false);
  assert.equal(emit(historyOf(["1.5", "2.0"])), false);
  assert.equal(sent.length, 0);
});

test("file : réseau indisponible conserve, 2xx retire, 4xx retire et compte un refus", async () => {
  let mode = "down";
  const fetchImpl = async () => {
    if (mode === "down") throw new Error("offline");
    if (mode === "ok") return { status: 201, ok: true, json: async () => ({ saved: 1 }) };
    return { status: 422, ok: false, json: async () => ({}) };
  };
  const queue = createTransitionQueue(fetchImpl);
  await queue.enqueue({ n: 1 });
  assert.equal(queue.status().pending, 1);
  assert.equal(queue.status().lastError, "api_unreachable");
  mode = "ok";
  await queue.flush();
  assert.equal(queue.status().pending, 0);
  assert.equal(queue.status().sent, 1);
  mode = "refused";
  await queue.enqueue({ n: 2 });
  assert.equal(queue.status().pending, 0);
  assert.equal(queue.status().rejected, 1);
});

test("file : bornée, les plus anciens éléments sont abandonnés et comptés", async () => {
  const queue = createTransitionQueue(async () => { throw new Error("offline"); }, 3);
  for (let i = 0; i < 5; i += 1) await queue.enqueue({ i });
  assert.equal(queue.status().pending, 3);
  assert.equal(queue.status().dropped, 2);
});