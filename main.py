import os
import uuid
import httpx
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Depends, Header, WebSocket, WebSocketDisconnect, Response
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from schemas import CallTriggerRequest, CallTriggerResponse, ZapiWebhookPayload
from services.zapi_client import ZapiClient
from services.traceability import (
    get_client_config,
    create_workflow_execution,
    update_workflow_status,
    record_step_execution
)
from services.database import init_db, save_voice_call_record
from services.audio_engine import VoiceSessionManager

load_dotenv()

# Logging Configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zapi_voice_service")

# Cache em memória para arquivos de áudio estáticos de cada chamada
audio_cache: Dict[str, bytes] = {}

app = FastAPI(
    title="Z-API Voice Service (MindFlow EDW)",
    description="Microserviço para orquestração de ligações de voz no WhatsApp via Z-API e OpenAI",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    """Inicializa as tabelas no PostgreSQL exclusivo da aplicação se a DATABASE_URL estiver presente."""
    await init_db()


@app.get("/health")
async def health_check():
    """Endpoint de verificação de saúde do serviço."""
    return {"status": "healthy", "service": "zapi_voice_service"}


from services.sip_engine import ZapiSipEngine


@app.get("/sip/info/{client_id}")
async def get_sip_credentials(client_id: str):
    """Consulta as credenciais SIP e status da conta Z-API para chamadas conversacionais."""
    client_cfg = await get_client_config(client_id)
    zapi_instance_id = client_cfg.get("zapi_instance_id")
    zapi_client_token = client_cfg.get("zapi_client_token")
    zapi_security_token = client_cfg.get("zapi_security_token")

    if not zapi_instance_id or not zapi_client_token or not zapi_security_token:
        raise HTTPException(status_code=400, detail="Credenciais Z-API não encontradas para o cliente.")

    sip_engine = ZapiSipEngine(zapi_instance_id, zapi_client_token, zapi_security_token)
    creds = await sip_engine.fetch_sip_credentials()
    return {"status": "success", "sip_credentials": creds}


# Registro global para rastreamento do ciclo de vida das chamadas (Auditoria de Testes)
call_tracker: Dict[str, Dict[str, Any]] = {}


@app.get("/call/status/{call_id}")
async def get_call_tracker_status(call_id: str):
    """Retorna o rastreamento em tempo real dos eventos e status da chamada."""
    if call_id in call_tracker:
        return call_tracker[call_id]
    raise HTTPException(status_code=404, detail="Chamada não encontrada no rastreador.")


@app.get("/call/tracker")
async def list_recent_tracked_calls():
    """Retorna as ultimas chamadas rastreadas no sistema."""
    return {"total": len(call_tracker), "calls": list(call_tracker.values())[-20:]}


@app.post("/webhook/call", response_model=CallTriggerResponse, status_code=202)
async def trigger_whatsapp_call(request: CallTriggerRequest):
    """
    Endpoint principal para disparar uma ligação via WhatsApp (Z-API).
    - Valida dados do cliente na client_configurations
    - Registra a capa mestre em workflow_executions e nós em workflow_step_executions
    - Executa POST /send-call na Z-API
    """
    workflow_name = request.workflow_name or "zapi_voice_service"
    
    # 1. Iniciar Rastreabilidade Mestre no Supabase EDW
    exec_id = await create_workflow_execution(
        workflow_name=workflow_name,
        input_data=request.model_dump(),
        execution_id=request.execution_id
    )
    
    try:
        await update_workflow_status(exec_id, "RUNNING")
        
        # 2. Nó 1: Validação de Entrada e Leitura de Configurações
        await record_step_execution(
            execution_id=exec_id,
            step_name=f"{workflow_name}_fetch_config",
            status="RUNNING",
            attempt=1,
            input_data={"client_id": request.client_id, "numero": request.numero}
        )
        
        client_cfg = await get_client_config(request.client_id)
        
        zapi_instance_id = client_cfg.get("zapi_instance_id")
        zapi_client_token = client_cfg.get("zapi_client_token")
        zapi_security_token = client_cfg.get("zapi_security_token")
        
        if not zapi_instance_id or not zapi_client_token or not zapi_security_token:
            err_msg = f"Credenciais Z-API incompletas para client_id {request.client_id}"
            await record_step_execution(
                execution_id=exec_id,
                step_name=f"{workflow_name}_fetch_config",
                status="FAILED",
                error_details=err_msg
            )
            await update_workflow_status(exec_id, "FAILED", error_details=err_msg)
            raise HTTPException(status_code=400, detail=err_msg)

        await record_step_execution(
            execution_id=exec_id,
            step_name=f"{workflow_name}_fetch_config",
            status="SUCCESS",
            output_data={"client_name": client_cfg.get("client_name"), "voice_id": client_cfg.get("voice_id", "nova")}
        )

        # 3. Nó 2: Envio da Chamada via Z-API
        await record_step_execution(
            execution_id=exec_id,
            step_name=f"{workflow_name}_send_call",
            status="RUNNING",
            attempt=1,
            input_data={"phone": request.numero}
        )

        zapi_client = ZapiClient(
            instance_id=zapi_instance_id,
            instance_token=zapi_client_token,
            security_token=zapi_security_token
        )
        
        voice_id = client_cfg.get("voice_id", "nova")
        prompt = request.contexto or "Você é o assistente virtual da MindFlow. Responda de forma natural, profissional e acolhedora."
        session = VoiceSessionManager(system_prompt=prompt, voice_id=voice_id)
        
        # Sintetizar a voz inicial do Agente de IA via OpenAI TTS
        greeting_text = "Olá! Tudo bem? Aqui é a assistente virtual da MindFlow. Como posso te ajudar hoje?"
        greeting_mp3 = await session.generate_speech_bytes(greeting_text)
        audio_cache[exec_id] = greeting_mp3

        base_public_url = os.getenv("BASE_PUBLIC_URL", "http://72.60.255.170:8000")
        default_audio_url = f"{base_public_url}/audio/{exec_id}.mp3"
        audio_url_to_use = request.call_audio_url or default_audio_url
        
        zapi_resp = await zapi_client.send_call(request.numero, call_audio_url=audio_url_to_use)
        zaap_id = zapi_resp.get("zaapId")
        message_id = zapi_resp.get("messageId")

        # Registrar no rastreador de auditoria e sessões ativas
        call_tracker[exec_id] = {
            "execution_id": exec_id,
            "phone": request.numero,
            "client_id": request.client_id,
            "zaap_id": zaap_id,
            "message_id": message_id,
            "status": "INITIATED",
            "created_at": datetime.utcnow().isoformat(),
            "zapi_response": zapi_resp,
            "events": []
        }
        
        active_sessions[exec_id] = {
            "session": session,
            "client_id": request.client_id,
            "phone": request.numero,
            "zaap_id": zaap_id,
            "start_time": datetime.utcnow()
        }

        await record_step_execution(
            execution_id=exec_id,
            step_name=f"{workflow_name}_send_call",
            status="SUCCESS",
            output_data=zapi_resp
        )

        # 4. Finalização Mestre com Sucesso
        output_data = {
            "status": "Accepted",
            "zaap_id": zaap_id,
            "message_id": message_id,
            "phone": request.numero,
            "execution_id": exec_id
        }
        await update_workflow_status(exec_id, "SUCCESS", output_data=output_data)

        return CallTriggerResponse(
            status="success",
            message="Ligação enfileirada e disparada com sucesso via Z-API.",
            execution_id=exec_id,
            zaap_id=zaap_id,
            message_id=message_id
        )

    except Exception as e:
        logger.error(f"Falha ao processar ligação: {str(e)}", exc_info=True)
        await update_workflow_status(exec_id, "FAILED", error_details=str(e))
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Erro interno no serviço de voz: {str(e)}")


# Gerenciador de sessões ativas por ID de execução/chamada
active_sessions: Dict[str, Dict[str, Any]] = {}


@app.post("/webhook/zapi/event")
async def handle_zapi_event(payload: Dict[str, Any]):
    """
    Webhook conversacional para tratamento de eventos de estado de chamada e áudio Z-API.
    - Recebe áudio do usuário, transcreve, gera resposta via LLM + OpenAI TTS.
    - Retorna a nova URL de áudio para continuar a conversa na chamada.
    - Grava o histórico final na tabela voice_calls do Postgres ao desligar.
    """
    logger.info(f"Evento Z-API recebido: {payload}")
    
    event_type = payload.get("event") or payload.get("type")
    phone = payload.get("phone") or payload.get("from")
    zaap_id = payload.get("zaapId") or payload.get("callId")
    status = payload.get("status")
    audio_url = payload.get("audioUrl") or payload.get("audio") or (payload.get("audio", {}) if isinstance(payload.get("audio"), dict) else {}).get("audioUrl")

    # Localizar sessão ativa por telefone ou zaap_id
    session_key = None
    for key, sess_data in list(active_sessions.items()):
        if sess_data.get("phone") == phone or sess_data.get("zaap_id") == zaap_id:
            session_key = key
            break

    # Registrar evento no call_tracker de auditoria
    if session_key and session_key in call_tracker:
        call_tracker[session_key]["events"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": event_type,
            "status": status,
            "payload": payload
        })
        if status:
            call_tracker[session_key]["status"] = status

    # Se recebeu áudio do usuário na ligação
    if audio_url and session_key and session_key in active_sessions:
        sess_info = active_sessions[session_key]
        session: VoiceSessionManager = sess_info["session"]
        
        try:
            logger.info(f"Baixando áudio do usuário da URL: {audio_url}")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(audio_url)
                if resp.status_code == 200:
                    user_audio_bytes = resp.content
                    
                    # 1. Transcrever com Whisper
                    user_text = await session.transcribe_audio_bytes(user_audio_bytes)
                    logger.info(f"Transcrição do usuário ({phone}): '{user_text}'")
                    
                    # 2. Processar resposta com LLM
                    assistant_text = await session.process_user_text_message(user_text)
                    logger.info(f"Resposta do Agente IA: '{assistant_text}'")
                    
                    # 3. Sintetizar áudio de resposta via OpenAI TTS
                    speech_bytes = await session.generate_speech_bytes(assistant_text)
                    
                    # 4. Salvar áudio em cache para a Z-API tocar
                    reply_audio_id = f"reply_{uuid.uuid4().hex}"
                    audio_cache[reply_audio_id] = speech_bytes
                    
                    base_public_url = os.getenv("BASE_PUBLIC_URL", "https://zapi-voice-service-api.bkpxmb.easypanel.host")
                    next_audio_url = f"{base_public_url}/audio/{reply_audio_id}.mp3"
                    
                    return {
                        "status": "success",
                        "callAudioUrl": next_audio_url,
                        "assistant_text": assistant_text
                    }
        except Exception as e:
            logger.error(f"Erro ao processar fala do usuário no webhook Z-API: {e}", exc_info=True)

    # Se a chamada foi encerrada/desconectada
    if status in ["DISCONNECTED", "ENDED", "CANCELLED", "COMPLETED"] and session_key in active_sessions:
        sess_info = active_sessions.pop(session_key, {})
        session: VoiceSessionManager = sess_info.get("session")
        start_time = sess_info.get("start_time", datetime.utcnow())
        end_time = datetime.utcnow()
        duration_seconds = int((end_time - start_time).total_seconds())

        if session:
            transcript = session.get_full_transcript()
            summary = await session.generate_call_summary()

            await save_voice_call_record(
                call_id=session_key,
                client_id=sess_info.get("client_id", "2"),
                phone_number=phone or sess_info.get("phone", ""),
                duration_seconds=duration_seconds,
                transcript=transcript,
                call_summary=summary,
                disconnection_reason=status or "user_hangup",
                status="completed"
            )
            logger.info(f"Histórico de chamada {session_key} gravado com sucesso no PostgreSQL próprio.")

    return {"status": "received", "event": event_type}


@app.websocket("/ws/audio/{client_id}")
async def websocket_audio_endpoint(websocket: WebSocket, client_id: str):
    """
    WebSocket endpoint para streaming de áudio bidirecional em tempo real.
    - Aguarda até 20 segundos por uma fala do usuário ('Alô?', etc.).
    - Se o usuário não falar em 20 segundos, a IA toma a iniciativa e faz a saudação inicial.
    """
    await websocket.accept()
    logger.info(f"Conexão WebSocket de áudio estabelecida para client_id {client_id}")
    try:
        client_cfg = await get_client_config(client_id)
        voice_id = client_cfg.get("voice_id", "nova")
        prompt = "Você é o assistente virtual da MindFlow. Responda com naturalidade e concisão."
        
        session = VoiceSessionManager(system_prompt=prompt, voice_id=voice_id)
        user_spoken = False

        # Primeira rodada: aguardar fala do usuário por até 20 segundos
        try:
            data = await asyncio.wait_for(websocket.receive_bytes(), timeout=20.0)
            if data:
                user_spoken = True
                user_text = await session.transcribe_audio_bytes(data)
                assistant_text = await session.process_user_text_message(user_text)
                speech_bytes = await session.generate_speech_bytes(assistant_text)
                await websocket.send_bytes(speech_bytes)
        except asyncio.TimeoutError:
            logger.info(f"Usuário não falou 'Alô' nos primeiros 20 segundos. IA iniciando a conversa para client_id {client_id}...")
            assistant_text = await session.generate_initial_greeting_if_silent()
            speech_bytes = await session.generate_speech_bytes(assistant_text)
            await websocket.send_bytes(speech_bytes)

        # Continuação da chamada
        while True:
            data = await websocket.receive_bytes()
            if data:
                user_text = await session.transcribe_audio_bytes(data)
                assistant_text = await session.process_user_text_message(user_text)
                speech_bytes = await session.generate_speech_bytes(assistant_text)
                await websocket.send_bytes(speech_bytes)

    except WebSocketDisconnect:
        logger.info(f"WebSocket encerrado pelo cliente {client_id}")
    except Exception as e:
        logger.error(f"Erro no WebSocket de áudio: {str(e)}", exc_info=True)
        await websocket.close()
