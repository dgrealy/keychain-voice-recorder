"""Speech-to-text (Whisper via mlx) and note structuring (Ollama).

Model names, prompt files and LLM options come from ``config.yaml``; see
:func:`voicenotes.processor.build_models`. ``mlx_whisper`` and ``ollama`` are imported
inside the methods so the rest of the package (and the tests) work without them.
"""
from __future__ import annotations

from pathlib import Path
import json
import logging

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Transcriber:
    """Transcribes audio with an mlx Whisper model.

    Parameters
    ----------
    speech_to_text_model : str
        Hugging Face repo or local path, e.g. ``"mlx-community/whisper-large-v3-mlx"``.
    language : str, optional
        Spoken language code.
    """

    def __init__(self, speech_to_text_model: str, language: str = "en"):
        self.speech_to_text_model = speech_to_text_model
        self.language = language

    def transcribe(self, audio_path: Path) -> str:
        """Return the transcript of ``audio_path`` (any format ffmpeg reads)."""
        import mlx_whisper

        logger.info(f"Transcribing audio file with {self.speech_to_text_model}...")

        transcript = mlx_whisper.transcribe(
            audio_path.as_posix(),
            path_or_hf_repo=self.speech_to_text_model,
            language=self.language,
        )
        logger.info("Transcription complete.")

        return transcript["text"].strip()

    @staticmethod
    def read_transcript(transcript_path: Path) -> str:
        """Read a saved transcript."""
        logger.info("Reading transcript file in /transcripts...")

        with open(transcript_path, "r") as f:
            transcript = f.read()

        return transcript

    @staticmethod
    def write_transcript(transcript: str, transcript_path: Path) -> None:
        """Save a transcript."""
        logger.info("Writing transcript file in /transcripts...")

        with open(transcript_path, "w") as f:
            f.write(transcript)


class Response(BaseModel):
    """Structured note returned by the LLM."""

    title: str
    content: str


class Translator:
    """Turns a raw transcript into a titled, cleaned-up note with an Ollama model.

    Parameters
    ----------
    llm : str
        Ollama model name, e.g. ``"gemma4:12b-mlx"``.
    system_prompt_path : Path
        Markdown file with the system prompt.
    examples_path : Path
        JSON list of ``{"user": <transcript>, "assistant": {"title", "content"}}`` few-shot examples.
    options : dict, optional
        Ollama generation options (temperature, num_ctx, ...).
    """

    def __init__(self, llm: str, system_prompt_path: Path, examples_path: Path, options: dict | None = None):
        self.llm = llm
        self.system_prompt_path = system_prompt_path
        self.examples_path = examples_path
        self.options = options or {}

    def get_messages(self, transcript: str) -> list[dict]:
        """Build the chat: system prompt, few-shot examples, then ``transcript``."""
        with open(self.system_prompt_path, "r") as f:
            system_prompt = f.read()

        with open(self.examples_path, "r") as f:
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

    def translate(self, transcript: str) -> Response:
        """Return the structured note for ``transcript``.

        Raises
        ------
        ConnectionError
            If the Ollama server is not running.
        """
        import ollama

        logger.info(f"Calling {self.llm} LLM...")

        response = ollama.chat(
            model=self.llm,
            messages=self.get_messages(transcript),
            format=Response.model_json_schema(),
            keep_alive=0,
            think=False,
            options=self.options,
        )
        logger.info("LLM call complete.")

        return Response.model_validate_json(response.message.content)
