import os
import logging
import time
from functools import lru_cache
from typing import Optional

import requests

from soup_corner.domain.model.address_extraction import AddressExtraction

logger = logging.getLogger(__name__)

ROUTES_API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ROUTES_API_TIMEOUT_SECONDS = 10

# Faixas de taxa de entrega do cardápio: (distância máxima em km, taxa em R$)
DELIVERY_FEES = (
    (1.0, 0.0),
    (2.5, 8.0),
    (4.0, 15.0),
)
MAX_DELIVERY_KM = DELIVERY_FEES[-1][0]


class MapsService:
    def __init__(self):
        self.maps_token = os.getenv("GOOGLE_MAPS_API_KEY")
        if not self.maps_token:
            raise ValueError("Defina GOOGLE_MAPS_API_KEY no arquivo .env")
        self.source_address = (os.getenv("SOURCE_ADDRESS") or "").strip()
        self.state_city = (os.getenv("STATE_CITY") or "").strip()
        if not self.source_address or not self.state_city:
            raise ValueError("Defina SOURCE_ADDRESS e STATE_CITY no arquivo .env")

        self.origin = f"{self.source_address}, {self.state_city}"
        # Sessão reaproveita a conexão HTTPS entre chamadas
        self.session = requests.Session()
        self.session.headers.update({
            "X-Goog-Api-Key": self.maps_token,
            # Pede só a distância: resposta menor e cobrança no SKU mais barato
            "X-Goog-FieldMask": "routes.distanceMeters",
        })

    def calculate_distance(self, address: AddressExtraction) -> tuple[bool, str]:
        """Retorna (entrega_atendida, texto).

        Se atendida, o texto traz distância e taxa para ser repassado ao LLM;
        caso contrário, é a mensagem enviada diretamente ao cliente.
        """
        destination = address.to_address_string()
        # Sem cidade, o Google pode achar a mesma rua em outra cidade
        if not address.city:
            destination = f"{destination}, {self.state_city}"

        km = self._route_distance_km(destination)
        if km is None:
            logger.warning("Endereço não localizado pela Routes API | %s", destination)
            return False, (
                f"Não consegui localizar o endereço {destination}. "
                "Pode conferir a rua, o número e o bairro?"
            )

        fee = self._delivery_fee(km)
        if fee is None:
            logger.info("Fora da área de entrega | %s | %.2f km", destination, km)
            return False, (
                f"Infelizmente não entregamos em {destination}: fica a {self._format_km(km)} km "
                f"e nossa área de entrega vai até {self._format_km(MAX_DELIVERY_KM)} km. 😔"
            )

        fee_text = "frete grátis" if fee == 0 else f"R$ {fee:.2f}".replace(".", ",")
        logger.info("Entrega atendida | %s | %.2f km | taxa=%s", destination, km, fee_text)
        return True, (
            f"\n\n[Informação do sistema: o endereço de entrega {destination} fica a "
            f"{self._format_km(km)} km; taxa de entrega: {fee_text}. Informe isso ao cliente.]"
        )

    # Cache: o cliente costuma repetir o endereço na conversa, e cada chamada à API é cobrada.
    # Exceções (falhas de rede/API) não são cacheadas
    @lru_cache(maxsize=256)
    def _route_distance_km(self, destination: str) -> Optional[float]:
        body = {
            "origin": {"address": self.origin},
            "destination": {"address": destination},
            "travelMode": "DRIVE",
            "regionCode": "BR",
        }
        start_time = time.perf_counter()
        response = self.session.post(ROUTES_API_URL, json=body, timeout=ROUTES_API_TIMEOUT_SECONDS)
        elapsed = time.perf_counter() - start_time

        if not response.ok:
            logger.error(
                "Erro na Routes API | status=%s | %.2fs | %s",
                response.status_code, elapsed, response.text[:500],
            )
            response.raise_for_status()

        routes = response.json().get("routes") or []
        if not routes or "distanceMeters" not in routes[0]:
            logger.debug("Routes API sem rota | %.2fs | %s", elapsed, destination)
            return None

        km = routes[0]["distanceMeters"] / 1000
        logger.debug("Routes API | %.2fs | %s | %.2f km", elapsed, destination, km)
        return km

    @staticmethod
    def _delivery_fee(km: float) -> Optional[float]:
        for max_km, fee in DELIVERY_FEES:
            if km <= max_km:
                return fee
        return None

    @staticmethod
    def _format_km(km: float) -> str:
        return f"{km:.1f}".replace(".", ",")
