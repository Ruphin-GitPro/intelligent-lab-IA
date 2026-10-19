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

const frameDiagnostics = createFrameDiagnosticStore();

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

if (typeof module !== "undefined" && module.exports) {
  module.exports = { API, createFrameDiagnosticStore, postHeartbeat };
}

if (typeof browser !== "undefined") {
  browser.runtime.onMessage.addListener((message, sender) => {
    if (message && message.type === "ail-heartbeat") return postHeartbeat();
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
