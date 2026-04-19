from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, Optional, Type, TypeVar
from urllib import request
from urllib.error import HTTPError, URLError

from pydantic import BaseModel, ValidationError

from src.config.loader import EngineConfig


TModel = TypeVar("TModel", bound=BaseModel)


class LLMServiceError(RuntimeError):
    pass


class LLMValidationError(LLMServiceError):
    def __init__(self, message: str, errors: Optional[list] = None) -> None:
        super().__init__(message)
        self.errors = errors or []


TransportCallable = Callable[[str, Dict[str, str], Dict[str, Any], int], Dict[str, Any]]
StreamTransportCallable = Callable[[str, Dict[str, str], Dict[str, Any], int], Iterable[str]]
IoRecorder = Callable[[Dict[str, Any]], None]


class LLMServiceBase:
    def __init__(
        self,
        config: EngineConfig,
        transport: Optional[TransportCallable] = None,
        stream_transport: Optional[StreamTransportCallable] = None,
        io_recorder: Optional[IoRecorder] = None,
    ) -> None:
        self.config = config
        self.transport = transport or self._default_transport
        self.stream_transport = stream_transport or self._default_stream_transport
        self.io_recorder = io_recorder

    def call_llm_json(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        user_payload: Dict[str, Any],
        output_model: Type[TModel],
        retry_budget: int,
        validation_feedback: Optional[str] = None,
    ) -> TModel:
        attempts = retry_budget + 1
        last_exc: Optional[Exception] = None
        dynamic_feedback = validation_feedback
        message_payload: Dict[str, Any] = dict(user_payload)

        for attempt_index in range(attempts):
            started_at = datetime.now(timezone.utc).isoformat()
            started = time.perf_counter()
            raw_payload: Optional[Dict[str, Any]] = None
            try:
                message_payload = dict(user_payload)
                if dynamic_feedback:
                    message_payload["validation_feedback"] = dynamic_feedback

                raw_payload = self._chat_completion(
                    agent_name=agent_name,
                    system_prompt=system_prompt,
                    user_payload=message_payload,
                    output_model=output_model,
                )
                parsed = output_model.model_validate(raw_payload)
                self._record_io(
                    {
                        "kind": "llm_call",
                        "status": "success",
                        "agent_name": agent_name,
                        "attempt_index": attempt_index,
                        "attempt_count": attempts,
                        "started_at": started_at,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        "system_prompt": system_prompt,
                        "user_payload": message_payload,
                        "response_model": output_model.__name__,
                        "raw_output": raw_payload,
                        "parsed_output": parsed.model_dump(mode="json"),
                    }
                )
                return parsed
            except ValidationError as exc:
                last_exc = exc
                dynamic_feedback = self._format_validation_feedback(exc)
                self._record_io(
                    {
                        "kind": "llm_call",
                        "status": "validation_error",
                        "agent_name": agent_name,
                        "attempt_index": attempt_index,
                        "attempt_count": attempts,
                        "started_at": started_at,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        "system_prompt": system_prompt,
                        "user_payload": message_payload,
                        "response_model": output_model.__name__,
                        "raw_output": raw_payload,
                        "validation_errors": exc.errors(),
                        "validation_feedback": dynamic_feedback,
                    }
                )
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
                last_exc = exc
                self._record_io(
                    {
                        "kind": "llm_call",
                        "status": "error",
                        "agent_name": agent_name,
                        "attempt_index": attempt_index,
                        "attempt_count": attempts,
                        "started_at": started_at,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        "system_prompt": system_prompt,
                        "user_payload": message_payload,
                        "response_model": output_model.__name__,
                        "raw_output": raw_payload,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )

        if isinstance(last_exc, ValidationError):
            raise LLMValidationError("LLM output validation failed", errors=last_exc.errors()) from last_exc
        if last_exc is not None:
            raise LLMServiceError(f"LLM call failed: {last_exc}") from last_exc
        raise LLMServiceError("LLM call failed with unknown error")

    def call_llm_stream_text(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        user_payload: Dict[str, Any],
        validation_feedback: Optional[str] = None,
    ) -> Iterator[str]:
        """Stream plain text output from an OpenAI-compatible chat endpoint."""

        started_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        message_payload = dict(user_payload)
        if validation_feedback:
            message_payload["validation_feedback"] = validation_feedback

        chunks: list[str] = []
        try:
            for chunk in self._chat_completion_stream_text(
                agent_name=agent_name,
                system_prompt=system_prompt,
                user_payload=message_payload,
            ):
                if not chunk:
                    continue
                chunks.append(chunk)
                yield chunk

            self._record_io(
                {
                    "kind": "llm_call",
                    "status": "success",
                    "agent_name": agent_name,
                    "attempt_index": 0,
                    "attempt_count": 1,
                    "started_at": started_at,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "system_prompt": system_prompt,
                    "user_payload": message_payload,
                    "response_model": "stream_text",
                    "stream_text": "".join(chunks),
                }
            )
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self._record_io(
                {
                    "kind": "llm_call",
                    "status": "error",
                    "agent_name": agent_name,
                    "attempt_index": 0,
                    "attempt_count": 1,
                    "started_at": started_at,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "system_prompt": system_prompt,
                    "user_payload": message_payload,
                    "response_model": "stream_text",
                    "partial_text": "".join(chunks),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            raise LLMServiceError(f"LLM streaming call failed: {exc}") from exc

    def _record_io(self, payload: Dict[str, Any]) -> None:
        if self.io_recorder is None:
            return
        try:
            self.io_recorder(payload)
        except Exception:
            # Logging must never break the agent flow.
            return

    def _chat_completion(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        user_payload: Dict[str, Any],
        output_model: Type[TModel],
    ) -> Dict[str, Any]:
        llm = self.config.llm
        api_key = getattr(llm, "api_key", "")
        if not api_key:
            raise LLMServiceError("llm.api_key is required")

        base_url = llm.api_base.rstrip("/")
        url = f"{base_url}/chat/completions"

        base_body = {
            "model": llm.model,
            "temperature": llm.temperature,
            "max_tokens": llm.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "metadata": {"agent_name": agent_name},
        }

        if not llm.enable_reasoning:
            # Compatibility flags for OpenAI-compatible providers (DashScope/Qwen, etc.).
            base_body["enable_thinking"] = False
            base_body["reasoning"] = {"enabled": False}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        structured_format = self._build_structured_response_format(agent_name, output_model)
        body = dict(base_body)
        body["response_format"] = structured_format

        try:
            response = self.transport(url, headers, body, llm.timeout)
        except HTTPError as exc:
            if not self._is_structured_output_not_supported(exc):
                raise
            # Fallback for providers that don't support json_schema yet.
            fallback = dict(base_body)
            fallback["response_format"] = {"type": "json_object"}
            response = self.transport(url, headers, fallback, llm.timeout)

        content = response["choices"][0]["message"]["content"]
        return self._extract_json_object(content)

    def _chat_completion_stream_text(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        user_payload: Dict[str, Any],
    ) -> Iterator[str]:
        llm = self.config.llm
        api_key = getattr(llm, "api_key", "")
        if not api_key:
            raise LLMServiceError("llm.api_key is required")

        base_url = llm.api_base.rstrip("/")
        url = f"{base_url}/chat/completions"

        body: Dict[str, Any] = {
            "model": llm.model,
            "temperature": llm.temperature,
            "max_tokens": llm.max_tokens,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "metadata": {"agent_name": agent_name},
        }

        if not llm.enable_reasoning:
            # Compatibility flags for OpenAI-compatible providers (DashScope/Qwen, etc.).
            body["enable_thinking"] = False
            body["reasoning"] = {"enabled": False}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        for line in self.stream_transport(url, headers, body, llm.timeout):
            stripped = str(line).strip()
            if not stripped:
                continue
            if not stripped.startswith("data:"):
                continue

            payload = stripped[5:].strip()
            if payload == "[DONE]":
                break

            packet = json.loads(payload)
            choices = packet.get("choices", [])
            if not choices:
                continue

            first = choices[0]
            delta = first.get("delta", {}) if isinstance(first, dict) else {}
            text = self._extract_stream_text_delta(delta)

            if not text and isinstance(first, dict):
                message = first.get("message", {})
                if isinstance(message, dict):
                    text = self._extract_stream_text_delta(message.get("content"))

            if text:
                yield text

    @staticmethod
    def _build_structured_response_format(agent_name: str, output_model: Type[TModel]) -> Dict[str, Any]:
        schema = output_model.model_json_schema()
        return {
            "type": "json_schema",
            "json_schema": {
                "name": f"{agent_name}_response",
                "strict": True,
                "schema": schema,
            },
        }

    @staticmethod
    def _is_structured_output_not_supported(exc: HTTPError) -> bool:
        try:
            body = exc.read().decode("utf-8", errors="ignore").lower()
        except Exception:
            body = ""

        # Conservative heuristic: unsupported response_format/json_schema style failures.
        return (
            exc.code in {400, 404, 422}
            and (
                "json_schema" in body
                or "response_format" in body
                or "not support" in body
                or "unsupported" in body
            )
        )

    @staticmethod
    def _extract_json_object(content: Any) -> Dict[str, Any]:
        if isinstance(content, dict):
            return content

        if not isinstance(content, str):
            raise ValueError("LLM content is not string")

        stripped = content.strip()
        candidates: list[str] = []

        fenced_matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.IGNORECASE | re.DOTALL)
        candidates.extend(fenced_matches)

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])

        if not candidates:
            raise ValueError("LLM content does not contain JSON object")

        last_error: Optional[Exception] = None
        for payload in candidates:
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(parsed, dict):
                return parsed
            last_error = ValueError("LLM JSON root must be object")

        if last_error is not None:
            raise ValueError(f"LLM content contains invalid JSON object: {last_error}") from last_error
        raise ValueError("LLM content does not contain JSON object")

    @staticmethod
    def _extract_stream_text_delta(value: Any) -> str:
        if isinstance(value, str):
            return value

        if isinstance(value, list):
            chunks: list[str] = []
            for item in value:
                if isinstance(item, str):
                    chunks.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", ""))
                if item_type == "text":
                    chunks.append(str(item.get("text", "")))
            return "".join(chunks)

        if isinstance(value, dict):
            if "content" in value:
                return LLMServiceBase._extract_stream_text_delta(value.get("content"))
            if "text" in value:
                return str(value.get("text", ""))

        return ""

    @staticmethod
    def _format_validation_feedback(exc: ValidationError) -> str:
        errors = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", []))
            errors.append(f"{loc}: {err.get('msg', 'invalid')}")
        return " ; ".join(errors)

    @staticmethod
    def _default_transport(url: str, headers: Dict[str, str], body: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = request.Request(url=url, data=data, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("response is not object")
            return parsed

    @staticmethod
    def _default_stream_transport(
        url: str,
        headers: Dict[str, str],
        body: Dict[str, Any],
        timeout_seconds: int,
    ) -> Iterator[str]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = request.Request(url=url, data=data, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            for raw_line in resp:
                if not raw_line:
                    continue
                yield raw_line.decode("utf-8", errors="ignore")
