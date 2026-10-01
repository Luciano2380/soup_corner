"""Testes integrados do MapsService.

A Routes API é substituída por um adapter falso montado na `requests.Session` do serviço, de modo
que cabeçalhos, corpo JSON, tratamento de status HTTP e cache são exercitados de ponta a ponta.
O teste marcado com `google_maps` chama a API real (cobrada) e só roda com RUN_GOOGLE_MAPS_TESTS=1.
"""
import os

import pytest
import requests

from soup_corner.domain.model.address_extraction import AddressExtraction
from soup_corner.adapters.outbound.maps_service import ROUTES_API_URL, MapsService

ORIGIN = "Rua do Restaurante, 123, Cidade Exemplo, Estado Exemplo"


def address(**fields) -> AddressExtraction:
    data = {"is_address": True, "street": "Rua das Flores", "number": "100", "neighborhood": "Centro"}
    data.update(fields)
    return AddressExtraction(**data)


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variable", ["GOOGLE_MAPS_API_KEY", "SOURCE_ADDRESS", "STATE_CITY"])
def test_init_requires_mandatory_variables(maps_env, monkeypatch, variable):
    monkeypatch.delenv(variable)
    with pytest.raises(ValueError, match=variable):
        MapsService()


def test_init_rejects_blank_variables(maps_env, monkeypatch):
    monkeypatch.setenv("SOURCE_ADDRESS", "   ")
    with pytest.raises(ValueError):
        MapsService()


def test_init_builds_origin_and_headers(maps_service):
    assert maps_service.origin == ORIGIN
    assert maps_service.session.headers["X-Goog-Api-Key"] == "chave-de-teste"
    assert maps_service.session.headers["X-Goog-FieldMask"] == "routes.distanceMeters"


# ---------------------------------------------------------------------------
# Requisição enviada à Routes API
# ---------------------------------------------------------------------------

def test_request_sent_to_routes_api(maps_service, routes_api):
    destination = "Rua das Flores, 100, Centro, Belo Horizonte - MG"
    routes_api.distances[destination] = 2.0

    maps_service.calculate_distance(address(city="Belo Horizonte", state="MG"))

    assert len(routes_api.requests) == 1
    request = routes_api.requests[0]
    assert request.method == "POST"
    assert request.url == ROUTES_API_URL
    assert request.headers["X-Goog-Api-Key"] == "chave-de-teste"
    assert request.headers["X-Goog-FieldMask"] == "routes.distanceMeters"
    assert routes_api.bodies[0] == {
        "origin": {"address": ORIGIN},
        "destination": {"address": destination},
        "travelMode": "DRIVE",
        "regionCode": "BR",
    }


def test_address_without_city_gets_shop_city(maps_service, routes_api):
    maps_service.calculate_distance(address())

    assert routes_api.bodies[0]["destination"]["address"] == "Rua das Flores, 100, Centro, Cidade Exemplo, Estado Exemplo"


# ---------------------------------------------------------------------------
# Faixas de taxa de entrega
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("km", "km_text", "fee_text"),
    [
        (0.5, "0,5", "frete grátis"),
        (1.0, "1,0", "frete grátis"),
        (1.001, "1,0", "R$ 8,00"),
        (2.5, "2,5", "R$ 8,00"),
        (3.2, "3,2", "R$ 15,00"),
        (4.0, "4,0", "R$ 15,00"),
    ],
)
def test_delivery_served_by_range(maps_service, routes_api, km, km_text, fee_text):
    destination = "Rua das Flores, 100, Centro, Cidade Exemplo, Estado Exemplo"
    routes_api.distances[destination] = km

    served, text = maps_service.calculate_distance(address())

    assert served is True
    assert text.startswith("\n\n[Informação do sistema:")
    assert f"fica a {km_text} km" in text
    assert f"taxa de entrega: {fee_text}" in text
    assert destination in text


def test_outside_delivery_area(maps_service, routes_api):
    routes_api.distances["Rua das Flores, 100, Centro, Cidade Exemplo, Estado Exemplo"] = 7.3

    served, text = maps_service.calculate_distance(address())

    assert served is False
    assert "Infelizmente não entregamos" in text
    assert "fica a 7,3 km" in text
    assert "vai até 4,0 km" in text


def test_address_not_found(maps_service, routes_api):
    served, text = maps_service.calculate_distance(address(street="Rua Inexistente"))

    assert served is False
    assert text.startswith("Não consegui localizar o endereço Rua Inexistente, 100, Centro")
    assert "Pode conferir a rua, o número e o bairro?" in text


# ---------------------------------------------------------------------------
# Erros HTTP e cache
# ---------------------------------------------------------------------------

def test_api_http_error_propagates_exception(maps_service, routes_api):
    routes_api.status_code = 403

    with pytest.raises(requests.HTTPError):
        maps_service.calculate_distance(address())


def test_http_error_is_not_cached(maps_service, routes_api):
    routes_api.status_code = 500
    with pytest.raises(requests.HTTPError):
        maps_service.calculate_distance(address())

    routes_api.status_code = 200
    routes_api.distances["Rua das Flores, 100, Centro, Cidade Exemplo, Estado Exemplo"] = 0.8
    served, _ = maps_service.calculate_distance(address())

    assert served is True
    assert len(routes_api.requests) == 2


def test_same_address_uses_cache(maps_service, routes_api):
    routes_api.distances["Rua das Flores, 100, Centro, Cidade Exemplo, Estado Exemplo"] = 2.0

    first = maps_service.calculate_distance(address())
    second = maps_service.calculate_distance(address())

    assert first == second
    assert len(routes_api.requests) == 1


def test_different_addresses_do_not_share_cache(maps_service, routes_api):
    maps_service.calculate_distance(address(number="100"))
    maps_service.calculate_distance(address(number="200"))

    assert len(routes_api.requests) == 2


# ---------------------------------------------------------------------------
# API real (opt-in, cobrada)
# ---------------------------------------------------------------------------

@pytest.mark.google_maps
@pytest.mark.skipif(os.getenv("RUN_GOOGLE_MAPS_TESTS") != "1", reason="defina RUN_GOOGLE_MAPS_TESTS=1 (API cobrada)")
def test_real_routes_api_shop_address():
    # Usa as credenciais e o endereço reais do .env
    service = MapsService()
    shop = AddressExtraction(is_address=True, street=service.source_address)

    served, text = service.calculate_distance(shop)

    assert served is True, text
    assert "frete grátis" in text
