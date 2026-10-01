from typing import Optional

from pydantic import BaseModel, Field


class AddressExtraction(BaseModel):
    """Endereço extraído de uma mensagem do cliente."""

    is_address: bool = Field(description="Indica se a mensagem contém um endereço")
    street: Optional[str] = Field(default=None, description="Rua, avenida, travessa, praça etc.")
    number: Optional[str] = Field(default=None, description="Número do imóvel")
    complement: Optional[str] = Field(default=None, description="Apartamento, bloco, casa, sala etc.")
    neighborhood: Optional[str] = Field(default=None, description="Bairro")
    city: Optional[str] = Field(default=None, description="Cidade")
    state: Optional[str] = Field(default=None, description="UF do estado, ex.: MG")
    zip_code: Optional[str] = Field(default=None, description="CEP no formato 00000-000")

    def to_address_string(self) -> str:        
        city_state = " - ".join(part for part in (self.city, self.state) if part)
        parts = (self.street, self.number, self.complement, self.neighborhood, city_state)
        return ", ".join(part.strip() for part in parts if part and part.strip())
