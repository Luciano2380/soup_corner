"""Fixtures compartilhadas pelos testes integrados.

- Os caminhos do `.env` são relativos à raiz do projeto: a sessão de testes roda a partir dela.
- A Google Routes API é cobrada: por padrão ela é substituída por um adapter HTTP falso montado
  na própria `requests.Session` do `MapsService` (o restante da pilha do `requests` é real).
- Os testes que usam o Ollama são pulados automaticamente se ele não estiver disponível.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import requests
from dotenv import load_dotenv
from requests.adapters import BaseAdapter

from soup_corner.adapters.outbound.maps_service import ROUTES_API_URL, MapsService

ROOT_DIR = Path(__file__).resolve().parents[2]

os.chdir(ROOT_DIR)
load_dotenv(ROOT_DIR / ".env")


# ---------------------------------------------------------------------------
# Google Routes API (falsa)
# ---------------------------------------------------------------------------

class FakeRoutesAdapter(BaseAdapter):
    """Responde às requisições da Routes API com distâncias configuradas por destino."""

    def __init__(self):
        super().__init__()
        self.distances: dict[str, float | None] = {}
        self.status_code = 200
        self.requests: list[requests.PreparedRequest] = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        destination = json.loads(request.body)["destination"]["address"]

        response = requests.Response()
        response.status_code = self.status_code
        response.url = request.url
        response.request = request
        if self.status_code != 200:
            payload = {"error": {"code": self.status_code, "message": "falha simulada"}}
        else:
            km = self.distances.get(destination)
            # Endereço desconhecido: a Routes API responde {} (sem rota)
            payload = {} if km is None else {"routes": [{"distanceMeters": round(km * 1000)}]}
        response._content = json.dumps(payload).encode()
        response.encoding = "utf-8"
        return response

    def close(self):
        pass

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.body) for r in self.requests]


@pytest.fixture
def maps_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "chave-de-teste")
    monkeypatch.setenv("SOURCE_ADDRESS", "Rua do Restaurante, 123")
    monkeypatch.setenv("STATE_CITY", "Cidade Exemplo, Estado Exemplo")


@pytest.fixture
def routes_api():
    return FakeRoutesAdapter()


@pytest.fixture(autouse=True)
def clear_distance_cache():
    # O lru_cache fica na classe e seria compartilhado entre os testes
    MapsService._route_distance_km.cache_clear()
    yield
    MapsService._route_distance_km.cache_clear()


@pytest.fixture
def maps_service(maps_env, routes_api):
    service = MapsService()
    service.session.mount(ROUTES_API_URL, routes_api)
    return service


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def _ollama_unavailable_reason() -> str | None:
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    if not host.startswith("http"):
        host = f"http://{host}"
    try:
        tags = requests.get(f"{host}/api/tags", timeout=3).json()
    except requests.RequestException:
        return f"Ollama indisponível em {host}"

    installed = {m["name"] for m in tags.get("models", [])}
    installed |= {name.removesuffix(":latest") for name in installed}
    required = [(os.getenv(var) or "").strip() for var in ("LLM_MODEL", "LLM_EMBEDDING")]
    missing = [name for name in required if not name or name not in installed]
    if missing:
        return f"Modelos ausentes no Ollama (LLM_MODEL/LLM_EMBEDDING): {missing}"
    return None


@pytest.fixture(scope="session")
def llm_service():
    """LLMService real, criado uma única vez (a indexação do cardápio é lenta)."""
    reason = _ollama_unavailable_reason()
    if reason:
        pytest.skip(reason)
    from soup_corner.adapters.outbound.llm_service import LLMService

    return LLMService()


# ---------------------------------------------------------------------------
# Telegram (objetos falsos de Update/Context)
# ---------------------------------------------------------------------------

@pytest.fixture
def telegram_env(monkeypatch, maps_env, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:token-de-teste")
    monkeypatch.setenv("HTML_TABLE_ORI_PATH", "resources/html/index.html")
    # Painel do dia é gerado em diretório temporário para não sujar data/pages
    monkeypatch.setenv("HTML_TABLE_DEST_PATH", str(tmp_path / "pages"))
    return tmp_path / "pages"


@pytest.fixture
def make_update():
    """Cria (update, context) mínimos, com reply_text/send_chat_action assíncronos gravando as chamadas."""

    def _make(text, user_id=4242, first_name="Maria", date=None):
        date = date or datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)
        message = SimpleNamespace(text=text, date=date, reply_text=AsyncMock())
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=user_id, first_name=first_name),
            effective_chat=SimpleNamespace(id=user_id),
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
        return update, context

    return _make

