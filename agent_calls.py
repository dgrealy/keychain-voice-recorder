from pathlib import Path
import json
import logging
from pydantic import BaseModel

import mlx_whisper
import ollama

from utils import format_custom_datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, speech_to_text_model: str = "mlx-community/whisper-large-v3-mlx"):
        self.speech_to_text_model = speech_to_text_model

    def transcribe(self, transcript_path: Path):
        logger.info(f"Transcribing audio file with {self.speech_to_text_model}...")

        transcript = mlx_whisper.transcribe(
            transcript_path.as_posix(),
            path_or_hf_repo=self.speech_to_text_model,
            language="en",
        )
        logger.info("Transcription complete.")

        return transcript["text"].strip()

    @staticmethod
    def read_transcript(transcript_path):
        logger.info("Reading transcript file in /transcripts...")

        with open(transcript_path, "r") as f:
            transcript = f.read()

        return transcript

    @staticmethod
    def write_transcript(transcript, transcript_path):
        logger.info("Writing transcript file in /transcripts...")

        with open(transcript_path, "w") as f:
            f.write(transcript)


class Response(BaseModel):
    title: str
    content: str


class Translator:
    def __init__(self, llm: str = "gemma4:12b-mlx"):
        self.llm = llm

    @staticmethod
    def get_messages(transcript):
        with open("./system_prompt.md", "r") as f:
            system_prompt = f.read()

        with open("./examples.json", "r") as f:
            examples = json.load(f)

        messages = [{"role": "system", "content": system_prompt}]
        for ex in examples:
            messages.append({"role": "user", "content": ex["user"]})
            messages.append({
                "role": "assistant",
                "content": json.dumps(ex["assistant"], ensure_ascii=False),
            })
        messages.append({"role": "user", "content": transcript})

        return messages

    def translate(self, transcript):
        logger.info(f"Calling {self.llm} LLM...")

        response = ollama.chat(
            model=self.llm,
            messages=self.get_messages(transcript),
            format=Response.model_json_schema(),
            keep_alive=0,
            think=False,
            options={
                "temperature": 0.4,
                "repeat_penalty": 1.0,
                "num_ctx": 8192
            }
        )
        logger.info("LLM call complete.")

        return Response.model_validate_json(response.message.content)
