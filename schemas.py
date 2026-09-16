from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class CallTriggerRequest(BaseModel):
    workflow_name: str = Field(default="zapi_voice_service", description="Nome do workflow acionador")
    execution_id: Optional[str] = Field(default=None, description="UUID único de rastreabilidade")
    client_id: str = Field(..., description="ID do cliente na tabela client_configurations (ex: '2')")
    numero: str = Field(..., description="Telefone do destinatário (ex: '+5548996027108')")
    contexto: Optional[str] = Field(default=None, description="Contexto dinâmico para o prompt da IA")
    quando_ligar: Optional[str] = Field(default=None, description="Timestamp ISO 8601 com fuso horário")
    call_audio_url: Optional[str] = Field(default=None, description="URL do arquivo de áudio para a Z-API tocar na chamada")


class CallTriggerResponse(BaseModel):
    status: str = Field(..., description="Estado do processamento (ex: 'success', 'accepted')")
    message: str = Field(..., description="Mensagem de retorno")
    execution_id: str = Field(..., description="UUID da execução no Supabase")
    zaap_id: Optional[str] = Field(default=None, description="ID interno da chamada na Z-API")
    message_id: Optional[str] = Field(default=None, description="ID da mensagem na Z-API")


class ZapiWebhookPayload(BaseModel):
    event: Optional[str] = Field(default=None, description="Nome do evento (ex: 'CallEvent', 'on-call-status')")
    phone: Optional[str] = Field(default=None, description="Número de telefone do evento")
    zaapId: Optional[str] = Field(default=None, description="zaapId da chamada")
    messageId: Optional[str] = Field(default=None, description="messageId da chamada")
    status: Optional[str] = Field(default=None, description="Status da chamada ('CONNECTED', 'DISCONNECTED', 'RINGING')")
    raw_data: Optional[Dict[str, Any]] = Field(default=None, description="Payload completo recebido")
