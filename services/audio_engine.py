import os
import io
import logging
from typing import Optional, Dict, Any, List
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("audio_engine")
logger.setLevel(logging.INFO)

_openai_client: Optional[AsyncOpenAI] = None

def get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("Openai_api_key")
        if not api_key:
            raise ValueError("OPENAI_API_KEY não configurada no ambiente.")
        _openai_client = AsyncOpenAI(api_key=api_key)
    return _openai_client


class VoiceSessionManager:
    """
    Gerencia a conversa por voz em tempo real:
    - Transcrição de áudio do usuário (Whisper)
    - Geração de resposta (GPT-4.1 / GPT-4o)
    - Síntese de áudio (OpenAI TTS com a voice_id configurada no cliente)
    """

    def __init__(self, system_prompt: str, voice_id: str = "nova", model: str = "gpt-4.1"):
        self.client = get_openai_client()
        self.system_prompt = system_prompt
        self.voice_id = voice_id
        self.model = model
        self.history: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]

    async def process_user_text_message(self, user_text: str) -> str:
        """Processa entrada em texto e retorna resposta gerada pelo LLM."""
        self.history.append({"role": "user", "content": user_text})
        
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=self.history,
            temperature=0.7
        )
        assistant_text = response.choices[0].message.content or ""
        self.history.append({"role": "assistant", "content": assistant_text})
        return assistant_text

    async def generate_initial_greeting_if_silent(self, prompt_override: Optional[str] = None) -> str:
        """Gera uma saudação iniciada pela IA caso o usuário não fale 'Alô' nos primeiros 20 segundos."""
        greeting_prompt = prompt_override or "O usuário atendeu a ligação mas não falou 'Alô' nos primeiros 20 segundos. Diga 'Olá! Tudo bem? Falo com o responsável?' de forma natural e amigável."
        self.history.append({"role": "system", "content": greeting_prompt})
        
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=self.history,
            temperature=0.7
        )
        assistant_text = response.choices[0].message.content or "Olá! Tudo bem? Falo com o responsável?"
        self.history.append({"role": "assistant", "content": assistant_text})
        return assistant_text

    async def transcribe_audio_bytes(self, audio_bytes: bytes, filename: str = "user_audio.wav") -> str:
        """Transcreve áudio binário do usuário via OpenAI Whisper."""
        buffer = io.BytesIO(audio_bytes)
        buffer.name = filename
        
        transcript = await self.client.audio.transcriptions.create(
            model="whisper-1",
            file=buffer,
            language="pt"
        )
        return transcript.text

    async def generate_speech_bytes(self, text: str) -> bytes:
        """Sintetiza texto em áudio via OpenAI TTS usando a voice_id do cliente."""
        response = await self.client.audio.speech.create(
            model="tts-1",
            voice=self.voice_id,  # 'nova', 'alloy', 'echo', etc.
            input=text,
            response_format="mp3"
        )
        return response.content

    def get_full_transcript(self) -> str:
        """Retorna a transcrição completa da conversa formatada."""
        lines = []
        for msg in self.history:
            role = msg.get("role")
            content = msg.get("content")
            if role == "user":
                lines.append(f"User: {content}")
            elif role == "assistant":
                lines.append(f"Agent: {content}")
        return "\n".join(lines)

    async def generate_call_summary(self) -> str:
        """Gera um resumo da ligação com base no histórico da conversa."""
        transcript = self.get_full_transcript()
        if not transcript.strip():
            return "Ligação encerrada sem interação de voz."
            
        summary_prompt = [
            {"role": "system", "content": "Você é um analisador de chamadas telefônicas. Escreva um resumo conciso (2 a 3 frases) em português dos pontos principais discutidos nesta conversa."},
            {"role": "user", "content": f"Transcrição da chamada:\n{transcript}"}
        ]
        
        res = await self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=summary_prompt,
            temperature=0.3
        )
        return res.choices[0].message.content or ""

