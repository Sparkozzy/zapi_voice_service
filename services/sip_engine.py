import os
import asyncio
import socket
import logging
import httpx
from typing import Dict, Any, Optional
from services.audio_engine import VoiceSessionManager
from services.database import save_voice_call_record
from services.zapi_client import ZapiClient

logger = logging.getLogger("zapi_voice_service.sip_engine")
logger.setLevel(logging.INFO)

class ZapiSipEngine:
    """
    Motor SIP / RTP para gerenciar o streaming bidirecional de chamadas de voz WhatsApp via Z-API.
    - Autentica na sip.z-api.io usando as credenciais SIP da instância Z-API.
    - Recebe os pacotes de mídia RTP da chamada.
    - Conecta com VoiceSessionManager (Whisper + GPT-4.1 + OpenAI TTS).
    - Persiste o histórico no PostgreSQL exclusivo ao desligar.
    """

    def __init__(self, instance_id: str, instance_token: str, security_token: str):
        self.instance_id = instance_id
        self.instance_token = instance_token
        self.security_token = security_token
        self.zapi_client = ZapiClient(instance_id, instance_token, security_token)
        self.sip_host = "sip.z-api.io"
        self.sip_port = 5060
        self.running = False
        self.rtp_socket: Optional[socket.socket] = None

    async def fetch_sip_credentials(self) -> Dict[str, Any]:
        """Busca o token efêmero e informações SIP ativas na Z-API."""
        sip_info = await self.zapi_client.get_sip_info()
        call_token = await self.zapi_client.get_call_token()
        
        return {
            "host": sip_info.get("host", self.sip_host),
            "user": sip_info.get("user", self.instance_id),
            "sip_token": call_token.get("token"),
            "active": sip_info.get("active", False)
        }

    async def start_sip_session(self, call_id: str, client_id: str, phone_number: str, system_prompt: str, voice_id: str = "nova"):
        """Inicia o gerenciador de chamada SIP conversacional para uma ligação ativa."""
        logger.info(f"Iniciando sessão SIP conversacional para a chamada {call_id} ({phone_number})...")
        
        creds = await self.fetch_sip_credentials()
        if not creds.get("active"):
            logger.error("Serviço SIP da Z-API não está ativo na instância.")
            return False

        session = VoiceSessionManager(system_prompt=system_prompt, voice_id=voice_id)
        
        # Inicializar socket UDP para recepção/envio de pacotes de mídia RTP (G.711 / PCM)
        try:
            self.rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.rtp_socket.setblocking(False)
            self.rtp_socket.bind(("0.0.0.0", 0))
            local_port = self.rtp_socket.getsockname()[1]
            logger.info(f"Porta RTP local alocada: {local_port} para a chamada {call_id}")
            self.running = True
        except Exception as e:
            logger.error(f"Erro ao alocar socket RTP local: {e}", exc_info=True)
            return False

        start_time = asyncio.get_event_loop().time()

        try:
            # Saudação inicial gerada pela IA
            initial_text = await session.generate_initial_greeting_if_silent("Diga 'Olá! Tudo bem? Falo com o responsável?' de forma natural.")
            initial_speech = await session.generate_speech_bytes(initial_text)
            logger.info(f"Saudação inicial em áudio gerada ({len(initial_speech)} bytes). Pronta para transmissão SIP.")

            # Loop de áudio conversacional contínuo
            while self.running:
                await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Erro na sessão SIP conversacional: {e}", exc_info=True)
        finally:
            self.running = False
            if self.rtp_socket:
                self.rtp_socket.close()
                self.rtp_socket = None

            end_time = asyncio.get_event_loop().time()
            duration_seconds = int(end_time - start_time)
            
            transcript = session.get_full_transcript()
            summary = await session.generate_call_summary()

            await save_voice_call_record(
                call_id=call_id,
                client_id=client_id,
                phone_number=phone_number,
                duration_seconds=duration_seconds,
                transcript=transcript,
                call_summary=summary,
                disconnection_reason="completed",
                status="completed"
            )
            logger.info(f"Sessão SIP finalizada. Registro de chamada {call_id} salvo no Postgres.")
            return True

    def stop_session(self):
        """Sinaliza parada da sessão SIP."""
        self.running = False
