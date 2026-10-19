"use strict";

const state = document.getElementById("state");
browser.runtime.sendMessage({ type: "ail-api-status" }).then((result) => {
  if (!result || !result.ok) {
    state.textContent = "API locale hors ligne — démarrez le serveur Python.";
    return;
  }
  state.textContent = result.collector_connected
    ? `Extension connectée (diagnostic uniquement) · ${result.round_count} manche(s) en base.`
    : `API connectée, extension Bet261 non détectée · ${result.round_count} manche(s) en base.`;
}).catch(() => {
  state.textContent = "API locale hors ligne — démarrez le serveur Python.";
});
