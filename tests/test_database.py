import pytest
import os
from unittest.mock import AsyncMock, patch
from services.database import save_voice_call_record, init_db

@pytest.mark.asyncio
async def test_save_voice_call_record_without_db_url():
    # Quando DATABASE_URL não está configurada, deve retornar False sem lançar erro
    with patch.dict(os.environ, {}, clear=True):
        result = await save_voice_call_record(
            call_id="call_test_123",
            client_id="2",
            phone_number="+555196506656",
            duration_seconds=45,
            transcript="User: Olá\nAgent: Oi!",
            call_summary="Conversa rápida de teste"
        )
        assert result is False

@pytest.mark.asyncio
async def test_init_db_mocked():
    mock_conn = AsyncMock()
    with patch("services.database.get_db_connection", return_value=mock_conn):
        await init_db()
        assert mock_conn.execute.called
        assert mock_conn.close.called
