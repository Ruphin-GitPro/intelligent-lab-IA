"""Tests d'intégration de l'API HTTP locale sur une base SQLite temporaire."""

from http.client import HTTPConnection
import json
import threading

import pytest

from aviator_lab.collection import api
from aviator_lab.collection.api import create_handler
from aviator_lab.storage import RoundRepository
from http.server import ThreadingHTTPServer


@pytest.fixture
def local_api(tmp_path, monkeypatch):
    repository = RoundRepository(tmp_path / "api.sqlite")
    repository.initialize()
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(repository))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(api, "_last_heartbeat", None)
    monkeypatch.setattr(api, "_last_collection_error", None)
    monkeypatch.setattr(api, "_last_collection_error_at", None)
    monkeypatch.setattr(api, "_collection_error_count", 0)
    try:
        yield server, repository
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        repository.close()


def request(server, method, path, payload=None, *, origin=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if origin:
        headers["Origin"] = origin
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    result = response.status, json.loads(response.read() or b"{}")
    connection.close()
    return result


def test_unverified_completed_claim_is_rejected_and_never_saved(local_api):
    server, repository = local_api
    payload = {
        "completed": True,
        "external_id": "observed-event-1",
        "multiplier": "2.375",
        "played_at": "2026-10-18T10:30:00Z",
    }
    status, body = request(server, "POST", "/api/v1/rounds", payload)
    assert status == 503
    assert body["error"] == "source_unverified"
    assert repository.list_rounds() == []
    assert request(server, "POST", "/api/v1/rounds", payload) == (
        503, {
            "error": "source_unverified",
            "detail": "Aucun adaptateur autorisé ne peut attester l'origine et la finalité de cette manche.",
        }
    )
    assert repository.list_rounds() == []


@pytest.mark.parametrize("payload", [
    {"completed": False, "external_id": "running-1", "multiplier": "5.3"},
    {"completed": True, "external_id": "invalid-1", "multiplier": "0.5"},
    {"completed": True, "external_id": "invalid-2", "multiplier": "NaN"},
    {"completed": True, "external_id": "invalid-3", "multiplier": "1.12345"},
    {"completed": True, "multiplier": "2.0"},
])
def test_api_refuses_all_rounds_until_a_verified_source_exists(local_api, payload):
    server, repository = local_api
    status, body = request(server, "POST", "/api/v1/rounds", payload)
    assert status == 503
    assert body["error"] == "source_unverified"
    assert repository.list_rounds() == []


def test_heartbeat_only_reports_connection_not_verified_collection(local_api):
    server, _repository = local_api
    assert request(server, "POST", "/api/v1/heartbeat", {"phase": "diagnostic"})[0] == 202
    status, body = request(server, "GET", "/api/v1/status")
    assert status == 200
    assert body["ok"] is True
    assert body["collection_status"] == "source_unverified"
    assert body["collector_connected"] is True
    assert body["round_count"] == 0
    assert body["latest_recorded_at"] is None


def test_real_origin_row_does_not_make_source_verified(local_api):
    server, repository = local_api
    repository.add_round("2.5", source="manual import", origin="real", external_id="not-bet261-proof")
    request(server, "POST", "/api/v1/heartbeat", {"phase": "diagnostic"})
    status, body = request(server, "GET", "/api/v1/status")
    assert status == 200
    assert body["round_count"] == 1
    assert body["collector_connected"] is True
    assert body["collection_status"] == "source_unverified"


def test_api_reports_collection_errors_and_recovers_connection(local_api):
    server, _repository = local_api
    status, body = request(server, "POST", "/api/v1/rounds", {
        "completed": True,
        "external_id": "unverified-round",
        "multiplier": "2.1",
    })
    assert status == 503
    assert body["error"] == "source_unverified"
    _status, error_body = request(server, "GET", "/api/v1/status")
    assert error_body["collector_connected"] is False
    assert error_body["last_collection_error"]
    assert error_body["last_collection_error_at"]
    assert error_body["collection_error_count"] == 1

    assert request(server, "POST", "/api/v1/heartbeat", {"phase": "diagnostic"})[0] == 202
    _status, recovered_body = request(server, "GET", "/api/v1/status")
    assert recovered_body["collector_connected"] is True
    assert recovered_body["collection_status"] == "source_unverified"
    assert recovered_body["round_count"] == 0
    assert recovered_body["last_collection_error"] == error_body["last_collection_error"]


def test_api_refuses_web_page_origin(local_api):
    server, repository = local_api
    status, _body = request(
        server,
        "POST",
        "/api/v1/rounds",
        {"completed": True, "external_id": "x", "multiplier": "2"},
        origin="https://evil.example",
    )
    assert status == 403
    assert repository.list_rounds() == []


def test_round_endpoint_cannot_turn_client_claim_into_verified_result(local_api):
    server, repository = local_api
    request(server, "POST", "/api/v1/heartbeat", {"phase": "diagnostic"})
    status, _body = request(server, "POST", "/api/v1/rounds", {
        "completed": True,
        "external_id": "same-value-is-not-proof",
        "multiplier": "3.75",
        "played_at": "2026-10-18T10:30:00Z",
    })
    _status, state = request(server, "GET", "/api/v1/status")
    assert status == 503
    assert state["collector_connected"] is True
    assert state["collection_status"] == "source_unverified"
    assert state["round_count"] == 0
    assert repository.list_rounds() == []


# --- Transitions d'historique (valeurs SYNTHÉTIQUES, uniquement pour tester le code) -------------------

EXTENSION = "moz-extension://test-extension"


def _window(start=0, size=30):
    """Fenêtre synthétique, la plus récente d'abord, sans périodicité."""
    return [f"{1.01 + ((i * 37) % 997) / 100:.2f}" for i in range(start, start + size)]


def _transition(previous, following, observed_at=None):
    from datetime import datetime, timezone

    return {
        "schema": 1,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "previous": previous,
        "next": following,
    }


def test_history_transition_saves_the_new_round_with_a_fingerprint_id(local_api):
    server, repository = local_api
    base = _window(1)
    following = [_window(0)[0]] + base[:-1]
    status, body = request(server, "POST", "/api/v1/history-transitions", _transition(base, following), origin=EXTENSION)
    assert (status, body["saved"], body["shift"]) == (201, 1, 1)
    rows = repository.list_rounds(origin="real")
    assert len(rows) == 1
    assert float(rows[0].multiplier) == float(following[0])
    assert rows[0].source == "firefox_history_window_v1"


def test_history_transition_retry_is_idempotent(local_api):
    server, repository = local_api
    base = _window(1)
    following = [_window(0)[0]] + base[:-1]
    payload = _transition(base, following)
    assert request(server, "POST", "/api/v1/history-transitions", payload, origin=EXTENSION)[0] == 201
    status, body = request(server, "POST", "/api/v1/history-transitions", payload, origin=EXTENSION)
    assert (status, body["saved"], body["duplicates"]) == (200, 0, 1)
    assert len(repository.list_rounds(origin="real")) == 1


def test_two_rounds_with_the_same_multiplier_stay_distinct(local_api):
    server, repository = local_api
    w0 = _window(2)
    w1 = ["2.00"] + w0[:-1]
    w2 = ["2.00"] + w1[:-1]
    assert request(server, "POST", "/api/v1/history-transitions", _transition(w0, w1), origin=EXTENSION)[0] == 201
    assert request(server, "POST", "/api/v1/history-transitions", _transition(w1, w2), origin=EXTENSION)[0] == 201
    assert len(repository.list_rounds(origin="real")) == 2


def test_shift_of_two_saves_two_rounds_oldest_first(local_api):
    server, repository = local_api
    base = _window(2)
    following = ["3.10", "1.50"] + base[:-2]
    status, body = request(server, "POST", "/api/v1/history-transitions", _transition(base, following), origin=EXTENSION)
    assert (status, body["saved"], body["shift"]) == (201, 2, 2)
    assert [float(r.multiplier) for r in repository.list_rounds(origin="real")] == [1.5, 3.1]


def test_no_change_saves_nothing(local_api):
    server, repository = local_api
    base = _window(1)
    status, body = request(server, "POST", "/api/v1/history-transitions", _transition(base, list(base)), origin=EXTENSION)
    assert (status, body["status"]) == (200, "no_change")
    assert repository.list_rounds(origin="real") == []


def test_unalignable_and_modified_history_is_refused(local_api):
    server, repository = local_api
    base = _window(1)
    modified = list(base)
    modified[5] = "9.99"  # une valeur déjà affichée ne doit jamais changer
    status, body = request(server, "POST", "/api/v1/history-transitions", _transition(base, modified), origin=EXTENSION)
    assert (status, body["error"]) == (422, "history_not_alignable")
    assert repository.list_rounds(origin="real") == []


def test_periodic_history_is_ambiguous_and_not_saved(local_api):
    server, repository = local_api
    base = ["1.50", "2.00"] * 15
    following = ["2.00", "1.50"] * 15
    status, body = request(server, "POST", "/api/v1/history-transitions", _transition(base, following), origin=EXTENSION)
    assert status == 422 and body["kind"] == "ambiguous"
    assert repository.list_rounds(origin="real") == []


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(schema=2),
    lambda p: p.update(previous=p["previous"][:5]),
    lambda p: p["next"].__setitem__(0, "0.50"),
    lambda p: p["next"].__setitem__(0, "NaN"),
    lambda p: p.update(observed_at="2026-10-18T10:00:00"),
    lambda p: p.update(observed_at="2020-01-01T00:00:00Z"),
])
def test_invalid_history_payload_is_refused(local_api, mutate):
    server, repository = local_api
    base = _window(1)
    payload = _transition(base, [_window(0)[0]] + base[:-1])
    mutate(payload)
    status, body = request(server, "POST", "/api/v1/history-transitions", payload, origin=EXTENSION)
    assert (status, body["error"]) == (400, "invalid_payload")
    assert repository.list_rounds(origin="real") == []


def test_history_transition_requires_an_extension_origin(local_api):
    server, repository = local_api
    base = _window(1)
    payload = _transition(base, [_window(0)[0]] + base[:-1])
    assert request(server, "POST", "/api/v1/history-transitions", payload)[0] == 403
    assert request(server, "POST", "/api/v1/history-transitions", payload, origin="https://evil.example")[0] == 403
    assert repository.list_rounds(origin="real") == []


def test_history_rounds_persist_after_restart(tmp_path):
    path = tmp_path / "persist.sqlite"
    first = RoundRepository(path)
    first.initialize()
    base = _window(1)
    from aviator_lab.collection.history import ingest_history_transition

    ingest_history_transition(first, _transition(base, [_window(0)[0]] + base[:-1]))
    first.close()
    second = RoundRepository(path)
    second.initialize()
    assert len(second.list_rounds(origin="real")) == 1
    second.close()