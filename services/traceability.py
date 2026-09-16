import os
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("traceability")
logger.setLevel(logging.INFO)

# Supabase Master Singleton
_master_client: Optional[Client] = None

def get_master_supabase() -> Client:
    global _master_client
    if _master_client is None:
        url = os.getenv("MASTER_SUPABASE_URL") or os.getenv("SUPABASE_URL")
        key = os.getenv("MASTER_SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("Credenciais do Supabase Master não configuradas no arquivo .env")
        
        # Sanitizar chave caso haja aspas ou duplicidade de caracteres do Easypanel
        key = key.strip('"\' \t\r\n')
        if "20622887713" in key:
            key = key.replace("20622887713", "2062288713")
            
        _master_client = create_client(url, key)
    return _master_client


async def get_client_config(client_id: str) -> Dict[str, Any]:
    """
    Busca as configurações do tenant na tabela client_configurations do Supabase Master.
    """
    master = get_master_supabase()
    res = master.table("client_configurations").select("*").eq("client_id", str(client_id)).execute()
    if not res.data:
        raise ValueError(f"Cliente id '{client_id}' não encontrado em client_configurations.")
    return res.data[0]


async def create_workflow_execution(workflow_name: str, input_data: Dict[str, Any], execution_id: Optional[str] = None) -> str:
    """
    Cria a capa mestre da execução na tabela workflow_executions.
    """
    master = get_master_supabase()
    exec_id = execution_id or str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc).isoformat()
    
    payload = {
        "id": exec_id,
        "workflow_name": workflow_name,
        "status": "PENDING",
        "input_data": input_data,
        "created_at": now_utc,
        "updated_at": now_utc
    }
    master.table("workflow_executions").insert(payload).execute()
    return exec_id


async def update_workflow_status(execution_id: str, status: str, output_data: Optional[Dict[str, Any]] = None, error_details: Optional[str] = None):
    """
    Atualiza o estado mestre da execução (RUNNING, SUCCESS, FAILED).
    """
    master = get_master_supabase()
    now_utc = datetime.now(timezone.utc).isoformat()
    payload = {
        "status": status,
        "updated_at": now_utc
    }
    if status == "RUNNING":
        payload["started_at"] = now_utc
    if status in ["SUCCESS", "FAILED"]:
        payload["completed_at"] = now_utc
    if output_data is not None:
        payload["output_data"] = output_data
    if error_details is not None:
        payload["error_details"] = error_details

    master.table("workflow_executions").update(payload).eq("id", execution_id).execute()


async def record_step_execution(execution_id: str, step_name: str, status: str, attempt: int = 1, input_data: Optional[Dict[str, Any]] = None, output_data: Optional[Dict[str, Any]] = None, error_details: Optional[str] = None):
    """
    Registra um nó/passo individual na tabela workflow_step_executions (Detalhe).
    """
    master = get_master_supabase()
    now_utc = datetime.now(timezone.utc).isoformat()
    
    payload = {
        "execution_id": execution_id,
        "step_name": step_name,
        "status": status,
        "attempt": attempt,
        "created_at": now_utc
    }
    if input_data is not None:
        payload["input_data"] = input_data
    if output_data is not None:
        payload["output_data"] = output_data
    if error_details is not None:
        payload["error_details"] = error_details

    master.table("workflow_step_executions").insert(payload).execute()


async def save_call_history_record(
    client_cfg: Dict[str, Any],
    to_number: str,
    zaap_id: str,
    transcript: str,
    duration_seconds: int,
    disconnection_reason: str = "user_hangup",
    call_summary: str = "",
    lead_name: str = "",
    lead_email: str = ""
):
    """
    Grava o registro detalhado de histórico da chamada na tabela Retell_calls_Mindflow do Supabase do Cliente.
    """
    supabase_url = client_cfg.get("supabase_url")
    supabase_key = client_cfg.get("supabase_service_key") or client_cfg.get("supabase_anon_key")
    
    if not supabase_url or not supabase_key:
        logger.warning("Supabase do cliente não configurado para gravação em Retell_calls_Mindflow.")
        return

    tenant_client = create_client(supabase_url, supabase_key)
    now_utc = datetime.now(timezone.utc).isoformat()
    agent_id = client_cfg.get("agent_id_ligacao_whatsapp") or client_cfg.get("retell_agent_id_ligacao_whatsapp") or "agent_zapi_voice"

    payload = {
        "created_at": now_utc,
        "Nome": lead_name or "Lead WhatsApp",
        "Email": lead_email or "",
        "Numero": to_number,
        "to_number": to_number,
        "from_number": client_cfg.get("whatsapp_phone_number", ""),
        "status": "connected" if duration_seconds > 0 else "not_connected",
        "call_id": zaap_id,
        "agent_id": agent_id,
        "agent_name": f"Agente WhatsApp Z-API ({client_cfg.get('client_name', 'Mindflow')})",
        "transcript": transcript,
        "disconnection_reason": disconnection_reason,
        "Duracao": str(duration_seconds),
        "call_summary": call_summary,
        "combined_cost": "0.01"  # Custo aproximado estimado por minuto
    }

    try:
        tenant_client.table("Retell_calls_Mindflow").insert(payload).execute()
        logger.info(f"Registro de chamada {zaap_id} salvo com sucesso em Retell_calls_Mindflow do cliente {client_cfg.get('client_id')}.")
    except Exception as e:
        logger.error(f"Erro ao salvar em Retell_calls_Mindflow: {str(e)}", exc_info=True)

