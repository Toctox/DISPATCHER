from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

MAX_PDF_BYTES = 250 * 1024 * 1024
TRAINING_BASE_URL = "https://api.treinamento.e-docs.es.gov.br"
PRODUCTION_BASE_URL = "https://api.e-docs.es.gov.br"


class EdocsError(RuntimeError):
    """Raised when E-Docs returns an invalid or unsuccessful response."""


@dataclass(frozen=True)
class EdocsDestination:
    name: str
    id: str


@dataclass(frozen=True)
class UploadTicket:
    url: str
    body: dict[str, str]
    temporary_id: str


def organizational_restriction() -> dict[str, Any]:
    return {
        "transparenciaAtiva": False,
        "idsFundamentosLegais": None,
        "classificacaoInformacao": None,
    }


class EdocsApi:
    """Small V2 client with no implicit write retries.

    The caller supplies an Authorization Code / Hybrid bearer token carrying
    the authenticated user's identity. Client-credentials tokens are not
    valid for authorship operations such as capture or forwarding.
    """

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = TRAINING_BASE_URL,
        timeout_seconds: float = 45.0,
    ) -> None:
        token = access_token.strip()
        if not token:
            raise ValueError("access_token is required")
        self.access_token = token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _api_request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        payload = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "FactoryDispatcher-Edocs/0.1",
        }
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = Request(url, data=payload, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                status = response.status
        except HTTPError as exc:
            raw = exc.read()
            detail = raw.decode("utf-8", errors="replace")[:4000]
            raise EdocsError(f"E-Docs HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise EdocsError(f"E-Docs transport failure: {exc.reason}") from exc

        if status not in expected:
            detail = raw.decode("utf-8", errors="replace")[:4000]
            raise EdocsError(f"E-Docs unexpected HTTP {status}: {detail}")
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise EdocsError("E-Docs returned non-JSON content") from exc

    @staticmethod
    def _uuid_from(value: Any, *preferred_keys: str) -> str:
        if isinstance(value, str):
            try:
                return str(uuid.UUID(value))
            except ValueError:
                pass
        if isinstance(value, dict):
            for key in preferred_keys:
                candidate = value.get(key)
                if candidate:
                    try:
                        return str(uuid.UUID(str(candidate)))
                    except ValueError:
                        continue
            for key, candidate in value.items():
                if "id" not in key.lower():
                    continue
                try:
                    return str(uuid.UUID(str(candidate)))
                except (ValueError, TypeError):
                    continue
        raise EdocsError("response did not contain a UUID")

    def current_user_id(self) -> str:
        data = self._api_request("GET", "/v2/usuario")
        return self._uuid_from(data, "id", "idUsuario", "idCidadao")

    def patriarcas(self) -> list[dict[str, Any]]:
        data = self._api_request("GET", "/v2/agente/patriarcas")
        return self._list_payload(data)

    def organizacoes(self, patriarca_id: str) -> list[dict[str, Any]]:
        data = self._api_request(
            "GET", f"/v2/agente/{uuid.UUID(patriarca_id)}/organizacoes"
        )
        return self._list_payload(data)

    def setores(self, orgao_id: str) -> list[dict[str, Any]]:
        data = self._api_request("GET", f"/v2/agente/{uuid.UUID(orgao_id)}/setores")
        return self._list_payload(data)

    @staticmethod
    def _list_payload(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("items", "result", "data", "agentes"):
                items = data.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
        raise EdocsError("E-Docs list response shape was not recognized")

    @staticmethod
    def _agent_name(item: dict[str, Any]) -> str:
        for key in ("nome", "name", "descricao", "sigla"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @classmethod
    def resolve_named_agent(
        cls,
        items: list[dict[str, Any]],
        *,
        contains: tuple[str, ...],
        expected_id: str | None = None,
    ) -> EdocsDestination:
        needles = tuple(x.casefold() for x in contains if x.strip())
        matches: list[dict[str, Any]] = []
        for item in items:
            name = cls._agent_name(item)
            folded = name.casefold()
            if all(needle in folded for needle in needles):
                matches.append(item)
        if len(matches) != 1:
            raise EdocsError(
                f"expected one agent matching {contains!r}, found {len(matches)}"
            )
        item = matches[0]
        agent_id = cls._uuid_from(item, "id", "idAgente", "idUnidade", "idOrganizacao")
        if expected_id and agent_id.lower() != str(uuid.UUID(expected_id)).lower():
            raise EdocsError(
                f"resolved agent id changed: expected {expected_id}, got {agent_id}"
            )
        return EdocsDestination(name=cls._agent_name(item), id=agent_id)

    def generate_upload_ticket(self, size_bytes: int) -> UploadTicket:
        if not (1 <= size_bytes <= MAX_PDF_BYTES):
            raise ValueError("PDF size must be between 1 byte and 250 MiB")
        data = self._api_request(
            "GET", f"/v2/documentos/upload-arquivo/gerar-url-upload/{size_bytes}"
        )
        if not isinstance(data, dict):
            raise EdocsError("upload ticket is not an object")
        url = data.get("url")
        body = data.get("body")
        temporary_id = data.get("identificadorTemporarioArquivoNaNuvem")
        if not isinstance(url, str) or not url.startswith("http"):
            raise EdocsError("upload ticket is missing url")
        if not isinstance(body, dict):
            raise EdocsError("upload ticket is missing body")
        if not isinstance(temporary_id, str) or not temporary_id.strip():
            raise EdocsError(
                "upload ticket is missing identificadorTemporarioArquivoNaNuvem"
            )
        return UploadTicket(
            url=url,
            body={str(k): str(v) for k, v in body.items()},
            temporary_id=temporary_id.strip(),
        )

    @staticmethod
    def validate_pdf(path: str | os.PathLike[str]) -> Path:
        file_path = Path(path).expanduser().resolve(strict=True)
        if file_path.suffix.lower() != ".pdf":
            raise ValueError(f"only PDF is accepted: {file_path.name}")
        size = file_path.stat().st_size
        if not (1 <= size <= MAX_PDF_BYTES):
            raise ValueError(f"PDF size out of range: {size} bytes")
        with file_path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise ValueError(f"file does not have a PDF signature: {file_path.name}")
        return file_path

    def upload_pdf(self, path: str | os.PathLike[str]) -> str:
        file_path = self.validate_pdf(path)
        ticket = self.generate_upload_ticket(file_path.stat().st_size)
        boundary = "----FactoryEdocs" + uuid.uuid4().hex

        chunks: list[bytes] = []
        for key, value in ticket.body.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )

        mime = mimetypes.guess_type(file_path.name)[0] or "application/pdf"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="file"; '
                    f'filename="{file_path.name}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {mime}\r\n\r\n".encode(),
                file_path.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        payload = b"".join(chunks)
        request = Request(
            ticket.url,
            data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(self.timeout_seconds, 120.0)) as response:
                status = response.status
                response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise EdocsError(f"storage upload HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise EdocsError(f"storage upload transport failure: {exc.reason}") from exc
        if status not in (200, 201, 204):
            raise EdocsError(f"storage upload unexpected HTTP {status}")
        return ticket.temporary_id

    def post_capture(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> str:
        if not endpoint.startswith("/v2/documentos/"):
            raise ValueError("capture endpoint must be a /v2/documentos/... route")
        data = self._api_request("POST", endpoint, body=payload, expected=(200, 202))
        return self._uuid_from(data, "idEvento", "idCapturaEvento")

    def validate_capture(
        self,
        *,
        mode: str,
        temporary_id: str,
        file_name: str,
        restriction: dict[str, Any] | None = None,
        credential_capturer: bool = True,
    ) -> dict[str, Any]:
        endpoint, payload = self._citizen_capture_request(
            mode=mode,
            temporary_id=temporary_id,
            file_name=file_name,
            restriction=restriction,
            credential_capturer=credential_capturer,
        )
        data = self._api_request(
            "POST", endpoint + "/validar", body=payload, expected=(200,)
        )
        if not isinstance(data, dict):
            raise EdocsError("capture validation response is not an object")
        if data.get("isSuccess") is False:
            raise EdocsError(
                "capture validation failed: "
                + str(data.get("message") or json.dumps(data, ensure_ascii=False))
            )
        return data

    def capture_citizen_file(
        self,
        *,
        mode: str,
        temporary_id: str,
        file_name: str,
        restriction: dict[str, Any] | None = None,
        credential_capturer: bool = True,
        validate_first: bool = True,
    ) -> str:
        endpoint, payload = self._citizen_capture_request(
            mode=mode,
            temporary_id=temporary_id,
            file_name=file_name,
            restriction=restriction,
            credential_capturer=credential_capturer,
        )
        if validate_first:
            self.validate_capture(
                mode=mode,
                temporary_id=temporary_id,
                file_name=file_name,
                restriction=restriction,
                credential_capturer=credential_capturer,
            )
        data = self._api_request("POST", endpoint, body=payload, expected=(200, 202))
        return self._uuid_from(data, "idEvento", "idCapturaEvento")

    @staticmethod
    def _citizen_capture_request(
        *,
        mode: str,
        temporary_id: str,
        file_name: str,
        restriction: dict[str, Any] | None,
        credential_capturer: bool,
    ) -> tuple[str, dict[str, Any]]:
        normalized = mode.strip().casefold().replace("_", "-")
        routes = {
            "digitalizado": "/v2/documentos/capturar/digitalizado/cidadao",
            "nato-digital-copia": "/v2/documentos/capturar/nato-digital/copia/cidadao",
            "icp-brasil": "/v2/documentos/capturar/nato-digital/icp-brasil/cidadao",
        }
        endpoint = routes.get(normalized)
        if endpoint is None:
            raise ValueError(
                "unsupported citizen capture mode; use digitalizado, "
                "nato-digital-copia or icp-brasil"
            )
        temp = temporary_id.strip()
        name = file_name.strip()
        if not temp:
            raise ValueError("temporary_id is required")
        if not name:
            raise ValueError("file_name is required")
        payload: dict[str, Any] = {
            "nomeArquivo": name,
            "credenciarCapturador": bool(credential_capturer),
            "restricaoAcesso": restriction or organizational_restriction(),
            "identificadorTemporarioArquivoNaNuvem": temp,
        }
        if normalized == "digitalizado":
            payload["valorLegal"] = "CopiaSimples"
        return endpoint, payload

    def poll_event(
        self,
        event_id: str,
        *,
        timeout_seconds: float = 180.0,
        interval_seconds: float = 2.0,
    ) -> dict[str, Any]:
        event_id = str(uuid.UUID(event_id))
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            data = self._api_request("GET", f"/v2/eventos/{event_id}")
            if not isinstance(data, dict):
                raise EdocsError("event response is not an object")
            last = data
            status = str(data.get("situacao", data.get("status", ""))).casefold()
            if status in {"executado", "concluido", "concluído", "success", "sucesso"}:
                return data
            if status in {"erro", "falha", "failed", "cancelado", "cancelled"}:
                raise EdocsError(f"E-Docs event failed: {json.dumps(data, ensure_ascii=False)}")
            time.sleep(interval_seconds)
        raise EdocsError(f"E-Docs event timeout; last={last!r}")

    @staticmethod
    def event_document_id(event: dict[str, Any]) -> str:
        value = event.get("idDocumento")
        if not value:
            raise EdocsError("completed capture event has no idDocumento")
        return str(uuid.UUID(str(value)))

    @staticmethod
    def event_forwarding_id(event: dict[str, Any]) -> str:
        value = event.get("idEncaminhamento")
        if not value:
            raise EdocsError("completed forwarding event has no idEncaminhamento")
        return str(uuid.UUID(str(value)))

    def create_forwarding(
        self,
        *,
        subject: str,
        message: str,
        responsible_id: str,
        destination_ids: list[str],
        document_ids: list[str],
        send_email_notifications: bool = True,
        restriction: dict[str, Any] | None = None,
    ) -> str:
        if not subject.strip():
            raise ValueError("subject is required")
        if not message.strip():
            raise ValueError("message is required")
        payload = {
            "assunto": subject.strip(),
            "idsDestinos": [str(uuid.UUID(x)) for x in destination_ids],
            "mensagem": message.strip(),
            "idResponsavel": str(uuid.UUID(responsible_id)),
            "idsDocumentos": [str(uuid.UUID(x)) for x in document_ids],
            "enviarEmailNotificacoes": bool(send_email_notifications),
            "restricaoAcesso": restriction or organizational_restriction(),
        }
        data = self._api_request(
            "POST", "/v2/encaminhamento/novo", body=payload, expected=(200, 202)
        )
        return self._uuid_from(data, "idEvento")
