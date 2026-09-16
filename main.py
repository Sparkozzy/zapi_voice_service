import os
import logging
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Depends, Header, WebSocket, WebSocketDisconnect
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
        
        zapi_resp = await zapi_client.send_call(request.numero)
        zaap_id = zapi_resp.get("zaapId")
        message_id = zapi_resp.get("messageId")

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


@app.post("/webhook/zapi/event")
async def handle_zapi_event(payload: Dict[str, Any]):
    """
    Webhook para tratamento de eventos de estado de chamada recebidos da Z-API.
    """
    logger.info(f"Evento Z-API recebido: {payload}")
    return {"status": "received", "event": payload.get("event")}


@app.websocket("/ws/audio/{client_id}")
async def websocket_audio_endpoint(websocket: WebSocket, client_id: str):
    """
    WebSocket endpoint para streaming de áudio bidirecional em tempo real.
    """
    await websocket.accept()
    logger.info(f"Conexão WebSocket de áudio estabelecida para client_id {client_id}")
    try:
        # Carregar prompt e configurações do cliente
        client_cfg = await get_client_config(client_id)
        voice_id = client_cfg.get("voice_id", "nova")
        prompt = "Você é o assistente virtual da MindFlow. Responda com naturalidade e concisão."
        
        session = VoiceSessionManager(system_prompt=prompt, voice_id=voice_id)

        while True:
            # Receber mensagem/áudio do cliente
            data = await websocket.receive_bytes()
            if data:
                # Transcrever entrada do usuário
                user_text = await session.transcribe_audio_bytes(data)
                # Gerar resposta de IA
                assistant_text = await session.process_user_text_message(user_text)
                # Sintetizar áudio de resposta
                speech_bytes = await session.generate_speech_bytes(assistant_text)
                # Enviar de volta pelo WebSocket
                await websocket.send_bytes(speech_bytes)

    except WebSocketDisconnect:
        logger.info(f"WebSocket encerrado pelo cliente {client_id}")
    except Exception as e:
        logger.error(f"Erro no WebSocket de áudio: {str(e)}", exc_info=True)
        await websocket.close()
