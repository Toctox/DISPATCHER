from __future__ import annotations

import uuid

import pytest

from factory_dispatcher.edocs_api import EdocsApi, EdocsError, organizational_restriction


def test_organizational_restriction_matches_v2_contract():
    assert organizational_restriction() == {
        "transparenciaAtiva": False,
        "idsFundamentosLegais": None,
        "classificacaoInformacao": None,
    }


def test_citizen_capture_payload_digitalizado_is_copia_simples():
    endpoint, payload = EdocsApi._citizen_capture_request(
        mode="digitalizado",
        temporary_id="2026/9/15/a_b.temp",
        file_name="documentos.pdf",
        restriction=None,
        credential_capturer=True,
    )
    assert endpoint == "/v2/documentos/capturar/digitalizado/cidadao"
    assert payload["valorLegal"] == "CopiaSimples"
    assert payload["identificadorTemporarioArquivoNaNuvem"] == "2026/9/15/a_b.temp"


@pytest.mark.parametrize(
    ("mode", "endpoint"),
    [
        ("nato-digital-copia", "/v2/documentos/capturar/nato-digital/copia/cidadao"),
        ("icp-brasil", "/v2/documentos/capturar/nato-digital/icp-brasil/cidadao"),
    ],
)
def test_citizen_capture_modes(mode, endpoint):
    actual, payload = EdocsApi._citizen_capture_request(
        mode=mode,
        temporary_id="tmp.temp",
        file_name="x.pdf",
        restriction=None,
        credential_capturer=True,
    )
    assert actual == endpoint
    assert "valorLegal" not in payload


def test_resolve_named_agent_detects_uuid_drift():
    expected = str(uuid.uuid4())
    actual = str(uuid.uuid4())
    with pytest.raises(EdocsError, match="resolved agent id changed"):
        EdocsApi.resolve_named_agent(
            [{"id": actual, "nome": "ARE ARACRUZ"}],
            contains=("ARE", "ARACRUZ"),
            expected_id=expected,
        )


def test_create_forwarding_uses_canonical_v2_shape(monkeypatch):
    api = EdocsApi("token")
    captured = {}
    event_id = str(uuid.uuid4())

    def fake_request(method, path, *, body=None, expected=(200,)):
        captured.update(method=method, path=path, body=body, expected=expected)
        return {"idEvento": event_id}

    monkeypatch.setattr(api, "_api_request", fake_request)
    responsible = str(uuid.uuid4())
    destination = str(uuid.uuid4())
    document = str(uuid.uuid4())
    assert api.create_forwarding(
        subject="Assunto",
        message="Mensagem",
        responsible_id=responsible,
        destination_ids=[destination],
        document_ids=[document],
    ) == event_id
    assert captured["path"] == "/v2/encaminhamento/novo"
    assert captured["body"] == {
        "assunto": "Assunto",
        "idsDestinos": [destination],
        "mensagem": "Mensagem",
        "idResponsavel": responsible,
        "idsDocumentos": [document],
        "enviarEmailNotificacoes": True,
        "restricaoAcesso": organizational_restriction(),
    }


def test_event_helpers_require_result_ids():
    with pytest.raises(EdocsError, match="idDocumento"):
        EdocsApi.event_document_id({"situacao": "Concluido"})
    with pytest.raises(EdocsError, match="idEncaminhamento"):
        EdocsApi.event_forwarding_id({"situacao": "Concluido"})
