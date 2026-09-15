import pytest
from fastapi.testclient import TestClient
import sys
import os

# Adicionar pasta raiz ao sys.path para importações de módulos
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "zapi_voice_service"}


def test_trigger_call_validation():
    # Enviar payload incompleto sem client_id ou numero
    payload = {
        "workflow_name": "zapi_voice_service"
    }
    response = client.post("/webhook/call", json=payload)
    assert response.status_code == 422  # Unprocessable Entity Pydantic
