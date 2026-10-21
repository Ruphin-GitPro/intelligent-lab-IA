"""API HTTP loopback pour une future source de manches validée."""

from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import logging
import time
from typing import Any
from urllib.parse import urlparse

from aviator_lab import config
from aviator_lab.collection.history import AlignmentError, PayloadError, ingest_history_transition  # AJOUT
from aviator_lab.storage import RoundRepository

HOST = "127.0.0.1"
PORT = 8765
_last_heartbeat: float | None = None
_last_collection_error: str | None = None
_last_collection_error_at: str | None = None
_collection_error_count = 0
_history_stats: dict[str, Any] = {  # AJOUT
    "saved_rounds": 0,
    "duplicate_rounds": 0,
    "rejected_transitions": 0,
    "last_saved_at": None,
}
_LOG = logging.getLogger("aviator_lab.collection")


def _record_collection_error(error: Exception | str) -> None:
    """Expose une erreur de collecte sans conserver le corps reçu ni de secrets."""
    global _last_collection_error, _last_collection_error_at, _collection_error_count
    _last_collection_error = str(error)[:500]
    _last_collection_error_at = datetime.now(timezone.utc).isoformat()
    _collection_error_count += 1
    _LOG.warning("Erreur de collecte locale: %s", _last_collection_error)


def create_handler(repository: RoundRepository):
    """Construit un handler isolé, pratique aussi pour les tests HTTP."""

    class LocalAPIHandler(BaseHTTPRequestHandler):
        server_version = "AviatorLocalAPI/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            _LOG.info("%s - %s", self.address_string(), fmt % args)

        def _send(self, status: int, data: dict[str, Any]) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            origin = self.headers.get("Origin", "")
            if origin.startswith("moz-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def _valid_host(self) -> bool:
            port = self.server.server_port
            return self.headers.get("Host", "") in {
                f"{HOST}:{port}", f"localhost:{port}"
            }

        def _valid_origin(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin.startswith("moz-extension://")

        # AJOUT : toute cette méthode est nouvelle
        def _handle_history_transition(self, payload: dict[str, Any]) -> None:
            """Enregistre une transition d'historique après alignement refait côté serveur."""
            if not (self.headers.get("Origin") or "").startswith("moz-extension://"):
                _record_collection_error("Transition refusée : une origine d'extension est requise.")
                self._send(403, {"error": "extension_origin_required"})
                return
            try:
                result = ingest_history_transition(repository, payload)
            except PayloadError as exc:
                _history_stats["rejected_transitions"] += 1
                _record_collection_error(exc)
                self._send(400, {"error": "invalid_payload", "detail": str(exc)})
                return
            except AlignmentError as exc:
                _history_stats["rejected_transitions"] += 1
                _record_collection_error(exc)
                self._send(422, {
                    "error": "history_not_alignable",
                    "kind": exc.kind,
                    "candidate_shifts": list(exc.candidate_shifts),
                })
                return
            except Exception:  # base inaccessible, disque plein... : jamais présenté comme un succès
                _LOG.exception("Échec d'enregistrement d'une transition d'historique.")
                _record_collection_error("Échec d'enregistrement dans SQLite.")
                self._send(500, {"error": "storage_error"})
                return
            _history_stats["saved_rounds"] += result.saved
            _history_stats["duplicate_rounds"] += result.duplicates
            if result.saved:
                _history_stats["last_saved_at"] = datetime.now(timezone.utc).isoformat()
            self._send(201 if result.saved else 200, {
                "accepted": True,
                "status": result.status,
                "saved": result.saved,
                "duplicates": result.duplicates,
                "shift": result.shift,
            })

        def do_OPTIONS(self) -> None:
            if not self._valid_host() or not self._valid_origin():
                self._send(403, {"error": "origin_refused"})
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "null"))
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "300")
            self.end_headers()

        def do_GET(self) -> None:
            if not self._valid_host() or not self._valid_origin():
                self._send(403, {"error": "origin_refused"})
                return
            if urlparse(self.path).path not in {"/health", "/api/v1/status"}:
                self._send(404, {"error": "not_found"})
                return
            rounds = repository.list_rounds(origin="real")
            recent = _last_heartbeat is not None and time.time() - _last_heartbeat < 90
            latest = rounds[-1] if rounds else None
            self._send(200, {
                "ok": True,
                "mode": "diagnostic_only",
                "collection_status": "source_unverified",
                "collector_connected": recent,
                "round_count": len(rounds),
                "latest_multiplier": str(latest.multiplier) if latest else None,
                "latest_external_id": latest.external_id if latest else None,
                "latest_recorded_at": latest.recorded_at.isoformat()
                if latest and latest.recorded_at else None,
                "last_heartbeat_age_seconds": round(time.time() - _last_heartbeat, 1)
                if _last_heartbeat else None,
                "last_collection_error": _last_collection_error,
                "last_collection_error_at": _last_collection_error_at,
                "collection_error_count": _collection_error_count,
                "history_ingest_enabled": True,  # AJOUT
                "history_ingest": dict(_history_stats),  # AJOUT
            })

        def do_POST(self) -> None:
            global _last_heartbeat
            if not self._valid_host() or not self._valid_origin():
                self._send(403, {"error": "origin_refused"})
                return
            path = urlparse(self.path).path
            if path not in {"/api/v1/rounds", "/api/v1/heartbeat", "/api/v1/history-transitions", "/api/v1/personal-bets"}:
                self._send(404, {"error": "not_found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = 0
            if not 1 <= size <= 65_536:
                _record_collection_error("Taille de requête invalide.")
                self._send(413 if size > 65_536 else 400, {"error": "invalid_body_size"})
                return
            if self.headers.get_content_type() != "application/json":
                _record_collection_error("La requête doit être au format JSON.")
                self._send(415, {"error": "json_required"})
                return
            try:
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("Un objet JSON est attendu.")
                if path == "/api/v1/heartbeat":
                    _last_heartbeat = time.time()
                    self._send(202, {"accepted": True})
                    return
                if path == "/api/v1/history-transitions":  # AJOUT
                    self._handle_history_transition(payload)  # AJOUT
                    return  # AJOUT
                if path == "/api/v1/personal-bets":
                    if not (self.headers.get("Origin") or "").startswith("moz-extension://"):
                        self._send(403, {"error": "extension_origin_required"})
                        return
                    rows = payload.get("bets")
                    if not isinstance(rows, list) or len(rows) > 100:
                        self._send(400, {"error": "invalid_bets"})
                        return
                    from datetime import datetime
                    from aviator_lab.storage import DuplicateBetError
                    saved = duplicates = 0
                    occurrences: dict[str, int] = {}
                    for row in rows:
                        if not isinstance(row, dict):
                            raise ValueError("Chaque pari doit être un objet JSON.")
                        identity = json.dumps(row, sort_keys=True, separators=(",", ":"))
                        occurrences[identity] = occurrences.get(identity, 0) + 1
                        external_id = "dom-" + hashlib.sha256(
                            f"{identity}:{occurrences[identity]}".encode()
                        ).hexdigest()
                        try:
                            repository.add_bet(
                                source="extension DOM Bet261", origin="real", external_id=external_id,
                                bet_at=datetime.fromisoformat(row["bet_at"]) if row.get("bet_at") else None,
                                stake_amount=row.get("stake_amount"), cashout_multiplier=row.get("cashout_multiplier"),
                                returned_amount=row.get("returned_amount"), currency=row.get("currency"),
                            )
                            saved += 1
                        except DuplicateBetError:
                            duplicates += 1
                    self._send(201 if saved else 200, {"accepted": True, "saved": saved, "duplicates": duplicates})
                    return
                # `completed: true` is client supplied and cannot authenticate either
                # provenance or finality. Keep this endpoint closed until a verified,
                # authorized source adapter is implemented.
                _record_collection_error("Résultat refusé : aucune source Bet261 vérifiée n'est connectée.")
                self._send(503, {
                    "error": "source_unverified",
                    "detail": "Aucun adaptateur autorisé ne peut attester l'origine et la finalité de cette manche.",
                })
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _record_collection_error(exc)
                self._send(400, {"error": "invalid_round", "detail": str(exc)})

    return LocalAPIHandler


def serve(host: str = HOST, port: int = PORT) -> None:
    """Démarre le serveur en boucle locale uniquement."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("L'API doit rester liée à la boucle locale.")
    repository = RoundRepository(config.DB_PATH)
    repository.initialize()
    server = ThreadingHTTPServer((host, port), create_handler(repository))
    server.daemon_threads = True
    _LOG.info("API locale prête sur http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        repository.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve()
