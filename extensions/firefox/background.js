"use strict";

const API = "http://127.0.0.1:8765";
const MAX_FRAME_DIAGNOSTICS = 20;

function createFrameDiagnosticStore(maxEntries = MAX_FRAME_DIAGNOSTICS, ttlMs = 20_000) {
  const entries = new Map();
  return {
    put(key, report, now = Date.now(), frameId = null) {
      entries.set(key, { report, receivedAt: now, frameId });
      while (entries.size > maxEntries) entries.delete(entries.keys().next().value);
    },
    list(now = Date.now()) {
      const cutoff = now - ttlMs;
      for (const [key, item] of entries) if (item.receivedAt < cutoff) entries.delete(key);
      return [...entries.values()]
        .filter((item) => item.report.documentContext?.frame === "embedded_frame")
        .map((item) => ({ ...item.report, extensionFrameId: item.frameId, receivedAt: new Date(item.receivedAt).toISOString() }));
    }
  };
}

/**
 * File d'attente en mémoire des transitions à envoyer. Un envoi réussi (2xx) ou un refus définitif (4xx)
 * retire l'élément; une erreur réseau ou 5xx le conserve pour une nouvelle tentative. L'envoi étant idempotent
 * côté serveur, une reprise ne crée pas de doublon. Limite : la file est perdue si Firefox arrête l'extension.
 */
function createTransitionQueue(fetchImpl = fetch, maxItems = 200) {
  const pending = [];
  const stats = { sent: 0, rejected: 0, retried: 0, dropped: 0, lastError: null, lastSavedRounds: 0 };
  let flushing = false;
  async function flush() {
    if (flushing) return;
    flushing = true;
    try {
      while (pending.length) {
        let response;
        try {
          response = await fetchImpl(`${API}/api/v1/history-transitions`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(pending[0]),
            cache: "no-store"
          });
        } catch (_error) {
          stats.retried += 1;
          stats.lastError = "api_unreachable";
          return;
        }
        if (response.status >= 500) {
          stats.retried += 1;
          stats.lastError = `http_${response.status}`;
          return;
        }
        if (response.ok) {
          stats.sent += 1;
          try { stats.lastSavedRounds += (await response.json()).saved || 0; } catch (_e) { /* corps illisible */ }
          stats.lastError = null;
        } else {
          stats.rejected += 1;
          stats.lastError = `http_${response.status}`;
        }
        pending.shift();
      }
    } finally {
      flushing = false;
    }
  }
  return {
    enqueue(transition) {
      pending.push(transition);
      while (pending.length > maxItems) { pending.shift(); stats.dropped += 1; }
      return flush();
    },
    flush,
    status: () => ({ pending: pending.length, ...stats })
  };
}

const frameDiagnostics = createFrameDiagnosticStore();
const transitionQueue = createTransitionQueue();
if (typeof browser !== "undefined") setInterval(() => { transitionQueue.flush(); }, 15_000);

async function postHeartbeat(fetchImpl = fetch) {
  try {
    const response = await fetchImpl(`${API}/api/v1/heartbeat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phase: "diagnostic" }),
      cache: "no-store"
    });
    return response.ok;
  } catch (_error) {
    return false;
  }
}

async function postPersonalBets(bets, fetchImpl = fetch) {
  try {
    const response = await fetchImpl(`${API}/api/v1/personal-bets`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bets }), cache: "no-store"
    });
    return response.ok;
  } catch (_error) { return false; }
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { API, createFrameDiagnosticStore, createTransitionQueue, postHeartbeat, postPersonalBets };
}

if (typeof browser !== "undefined") {
  browser.runtime.onMessage.addListener((message, sender) => {
    if (message && message.type === "ail-heartbeat") return postHeartbeat();
    if (message && message.type === "ail-personal-bets" && Array.isArray(message.bets)) return postPersonalBets(message.bets);
    if (message && message.type === "ail-history-transition" && message.transition) {
      transitionQueue.enqueue(message.transition);
      return Promise.resolve({ queued: true });
    }
    if (message && message.type === "ail-transition-status") return Promise.resolve(transitionQueue.status());
    if (message && message.type === "ail-frame-diagnostic" && message.report && message.report.documentContext) {
      const tabId = sender && sender.tab && Number.isInteger(sender.tab.id) ? sender.tab.id : "unknown-tab";
      const frameId = sender && Number.isInteger(sender.frameId) ? sender.frameId : "unknown-frame";
      const key = `${tabId}:${frameId}`;
      frameDiagnostics.put(key, message.report, Date.now(), frameId);
      return Promise.resolve({ accepted: true });
    }
    if (message && message.type === "ail-get-frame-diagnostics") {
      return Promise.resolve(frameDiagnostics.list());
    }
    if (message && message.type === "ail-api-status") {
      return fetch(`${API}/api/v1/status`, { cache: "no-store" })
        .then((response) => response.json())
        .catch(() => ({ ok: false, collector_connected: false }));
    }
    return undefined;
  });
}
