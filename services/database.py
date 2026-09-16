import os
import logging
import asyncpg
from typing import Optional, Dict, Any

logger = logging.getLogger("zapi_voice_service.database")

DATABASE_URL = os.getenv("DATABASE_URL")

async def get_db_connection():
    """Retorna uma conexão assíncrona com o PostgreSQL exclusivo do zapi_voice_service."""
    if not DATABASE_URL:
        logger.warning("DATABASE_URL não configurada. Histórico próprio não será salvo no Postgres.")
        return None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Erro ao conectar ao PostgreSQL exclusivo: {e}")
        return None

async def init_db():
    """Inicializa as tabelas necessárias no PostgreSQL exclusivo da aplicação."""
    conn = await get_db_connection()
    if not conn:
        return
    try:
        query = """
        CREATE TABLE IF NOT EXISTS voice_calls (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            call_id VARCHAR(255) UNIQUE NOT NULL,
            client_id VARCHAR(100) NOT NULL,
            phone_number VARCHAR(50) NOT NULL,
            duration_seconds INTEGER DEFAULT 0,
            transcript TEXT,
            call_summary TEXT,
            disconnection_reason VARCHAR(100),
            status VARCHAR(50) DEFAULT 'completed',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_voice_calls_client_id ON voice_calls(client_id);
        CREATE INDEX IF NOT EXISTS idx_voice_calls_call_id ON voice_calls(call_id);
        """
        await conn.execute(query)
        logger.info("Tabela 'voice_calls' verificada/criada com sucesso no PostgreSQL próprio.")
    except Exception as e:
        logger.error(f"Erro ao inicializar schema no PostgreSQL próprio: {e}")
    finally:
        await conn.close()

async def save_voice_call_record(
    call_id: str,
    client_id: str,
    phone_number: str,
    duration_seconds: int,
    transcript: str,
    call_summary: str,
    disconnection_reason: str = "user_hangup",
    status: str = "completed"
) -> bool:
    """Insere ou atualiza um registro de chamada de voz na tabela voice_calls do Postgres próprio."""
    conn = await get_db_connection()
    if not conn:
        return False
    try:
        query = """
        INSERT INTO voice_calls (
            call_id, client_id, phone_number, duration_seconds, transcript, call_summary, disconnection_reason, status
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (call_id) DO UPDATE SET
            duration_seconds = EXCLUDED.duration_seconds,
            transcript = EXCLUDED.transcript,
            call_summary = EXCLUDED.call_summary,
            disconnection_reason = EXCLUDED.disconnection_reason,
            status = EXCLUDED.status,
            updated_at = CURRENT_TIMESTAMP;
        """
        await conn.execute(
            query,
            call_id,
            str(client_id),
            phone_number,
            duration_seconds,
            transcript,
            call_summary,
            disconnection_reason,
            status
        )
        logger.info(f"Registro da chamada {call_id} salvo com sucesso no PostgreSQL próprio.")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar registro de chamada no Postgres próprio: {e}", exc_info=True)
        return False
    finally:
        await conn.close()
