from typing import Optional

from pydantic import BaseModel, Field


class OrderExtraction(BaseModel):    

    completion_status: bool = Field(default=False, description="Indica se finalizou pedido")
    client: Optional[str] = Field(default=None, description="Nome cliente.")
    order: Optional[str] = Field(default=None, description="Descricao Pedido")
    amount: Optional[str] = Field(default=None, description="Valor Pedido")
    address: Optional[str] = Field(default=None, description="Endereço de entrega pedido")
    message: Optional[str] = Field(default=None, description="Messagem resposta")    
