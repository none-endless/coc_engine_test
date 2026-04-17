from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Type, TypeVar
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


class LLMServiceBase:
    def __init__(self, config: EngineConfig, transport: Optional[TransportCallable] = None) -> None:
        self.config = config
        self.transport = transport or self._default_transport

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

        for _ in range(attempts):
            try:
                message_payload = dict(user_payload)
                if dynamic_feedback:
                    message_payload["validation_feedback"] = dynamic_feedback

                raw = self._chat_completion(
                    agent_name=agent_name,
                    system_prompt=system_prompt,
                    user_payload=message_payload,
                    output_model=output_model,
                )
                return output_model.model_validate(raw)
            except ValidationError as exc:
                last_exc = exc
                dynamic_feedback = self._format_validation_feedback(exc)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
                last_exc = exc

        if isinstance(last_exc, ValidationError):
            raise LLMValidationError("LLM output validation failed", errors=last_exc.errors()) from last_exc
        if last_exc is not None:
            raise LLMServiceError(f"LLM call failed: {last_exc}") from last_exc
        raise LLMServiceError("LLM call failed with unknown error")

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
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM content does not contain JSON object")

        payload = stripped[start : end + 1]
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            raise ValueError("LLM JSON root must be object")
        return parsed

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
