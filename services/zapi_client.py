import logging
import httpx
from typing import Dict, Any, Optional

logger = logging.getLogger("zapi_client")
logger.setLevel(logging.INFO)


class ZapiClient:
    """Cliente HTTP assíncrono para integração com as APIs de Chamada da Z-API."""

    BASE_URL = "https://api.z-api.io/instances"

    def __init__(self, instance_id: str, instance_token: str, security_token: str):
        self.instance_id = instance_id
        self.instance_token = instance_token
        self.security_token = security_token

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Client-Token": self.security_token,
            "Content-Type": "application/json"
        }

    def _clean_phone(self, phone: str) -> str:
        """Sanitiza o telefone removendo '+' e caracteres não numéricos."""
        cleaned = "".join(filter(str.isdigit, phone))
        return cleaned

    async def get_whatsapp_phone_jid(self, phone: str) -> str:
        """Consulta o servidor do WhatsApp via Z-API para obter o número exato cadastrado (JID)."""
        clean_number = self._clean_phone(phone)
        url = f"{self.BASE_URL}/{self.instance_id}/token/{self.instance_token}/phone-exists/{clean_number}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("exists") and data.get("phone"):
                        exact_phone = data.get("phone")
                        logger.info(f"JID WhatsApp resolvido com sucesso: de '{clean_number}' para '{exact_phone}'")
                        return exact_phone
        except Exception as e:
            logger.warning(f"Não foi possível consultar phone-exists para {clean_number}: {e}")
        return clean_number

    async def send_call(self, phone: str, call_audio_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Dispara um sinal de ligação via Z-API POST /send-call.
        Resolve automaticamente o JID do WhatsApp e requer callAudioUrl.
        """
        clean_number = await self.get_whatsapp_phone_jid(phone)
        url = f"{self.BASE_URL}/{self.instance_id}/token/{self.instance_token}/send-call"
        payload = {"phone": clean_number}
        if call_audio_url:
            payload["callAudioUrl"] = call_audio_url

        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info(f"Enviando send-call Z-API para {clean_number} (instância: {self.instance_id}, callAudioUrl: {call_audio_url})")
            response = await client.post(url, json=payload, headers=self._get_headers())
            
            if response.status_code != 200:
                logger.error(f"Erro na Z-API send-call: HTTP {response.status_code} - {response.text}")
                response.raise_for_status()
                
            return response.json()

    async def get_call_token(self) -> Dict[str, Any]:
        """
        Gera um token efêmero para conexões de áudio/WebRTC.
        """
        url = f"{self.BASE_URL}/{self.instance_id}/token/{self.instance_token}/call-token"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=self._get_headers())
            response.raise_for_status()
            return response.json()

    async def get_sip_info(self) -> Dict[str, Any]:
        """
        Consulta o status do serviço de chamadas da instância.
        """
        url = f"{self.BASE_URL}/{self.instance_id}/token/{self.instance_token}/sip-info"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=self._get_headers())
            response.raise_for_status()
            return response.json()
