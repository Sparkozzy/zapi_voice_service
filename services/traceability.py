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
