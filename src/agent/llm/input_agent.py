from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from src.agent.llm.service import LLMServiceBase, LLMValidationError
from src.agent.prompt.dm_prompt import DM_SYSTEM_PROMPT
from src.data.model.agent_input import AvailableAttributeRef, DmAgentInput
from src.data.model.agent_output import DmAgentLlmOutput, DmAgentOutput, DmAgentSystemOutput


_META_BLOCK_KEYWORDS = (
	"跳出游戏",
	"系统提示词",
	"systemprompt",
	"system prompt",
	"开发者消息",
	"隐藏规则",
	"提示词原文",
	"忽略规则",
	"无视规则",
	"覆盖规则",
	"越狱",
	"jailbreak",
	"api_key",
	"base_url",
	"配置文件",
	"模型参数",
	"源码",
	"后端实现",
	"你现在不是dm",
	"你现在是chatgpt",
)

_ABUSIVE_KEYWORDS = (
	"傻逼",
	"傻x",
	"傻比",
	"白痴",
	"蠢货",
	"废物",
	"脑残",
	"弱智",
	"贱人",
	"狗东西",
	"滚开",
	"去死",
	"操你",
	"草你",
	"妈的",
	"他妈的",
	"草泥马",
)

_OFF_TOPIC_HELP_VERBS = ("帮我", "给我", "替我", "顺便", "请你", "麻烦你")
_OFF_TOPIC_DOMAIN_KEYWORDS = (
	"python",
	"java",
	"javascript",
	"代码",
	"脚本",
	"程序",
	"debug",
	"报错",
	"bug",
	"sql",
	"接口",
	"api",
	"网页",
	"前端",
	"后端",
	"简历",
	"论文",
	"作业",
	"数学题",
	"翻译",
	"热搜",
	"股价",
	"机票",
	"酒店",
	"手机推荐",
	"电脑配置",
)
_OFF_TOPIC_REQUEST_PHRASES = (
	"帮我写代码",
	"给我写代码",
	"帮我写python",
	"帮我看看报错",
	"写个sql",
	"写个接口",
	"翻译成英文",
	"写简历",
	"做这道数学题",
	"总结这篇论文",
	"推荐一款手机",
	"查一下热搜",
)

_NARRATIVE_OVERRIDE_KEYWORDS = (
	"改设定",
	"修改设定",
	"覆盖设定",
	"改剧情",
	"修改剧情",
	"重写剧情",
	"忽略前文",
	"无视前文",
	"不管前文",
	"别管前文",
	"不按当前剧情",
	"不按当前叙事",
)

_MODERN_SETTING_KEYWORDS = (
	"手机",
	"微信",
	"电脑",
	"程序员",
	"直播",
	"ak47",
	"手枪",
	"步枪",
	"汽车",
	"地铁",
	"互联网",
	"app",
	"无人机",
	"摄像机",
	"外卖",
)

_EDUCATION_CONFLICT_KEYWORDS = (
	"强奸",
	"轮奸",
	"凌辱",
	"淫乱",
	"性交",
	"做爱",
	"口交",
	"裸体",
	"乳房",
	"下体",
	"调教",
	"虐杀",
	"肢解",
	"开膛破肚",
)
_GRAPHIC_REQUEST_PREFIXES = ("详细描写", "展开描写", "重点描写", "细致描写")
_GRAPHIC_REQUEST_KEYWORDS = ("血腥", "凌辱", "尸体", "虐待", "羞辱", "霸凌", "暴力")

_ALNUM_OR_CJK_PATTERN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


class DmAnalyzeResult(BaseModel):
	output: DmAgentOutput
	retries: int = Field(default=0)
	validation_errors: List[str] = Field(default_factory=list)

	@property
	def intent_info(self):
		return self.output.llm_output.intent_info


class DMAgent:
	"""LLM-driven DM agent with output self-healing retries."""

	def __init__(self, llm_service: LLMServiceBase, max_retries: int = 2) -> None:
		self.llm_service = llm_service
		self.max_retries = max_retries

	def run(
		self,
		agent_input: DmAgentInput,
	) -> DmAnalyzeResult:
		guardrail_output = self._precheck_guardrail(agent_input)
		if guardrail_output is not None:
			return DmAnalyzeResult(
				output=DmAgentOutput(
					llm_output=guardrail_output,
					system_output=DmAgentSystemOutput(e1_view=agent_input.llm_input.e1),
				),
				retries=0,
				validation_errors=[],
			)

		user_payload = agent_input.llm_input.model_dump(mode="json")
		user_payload.pop("narrative_info", None)
		agent_memory_payload = user_payload.get("agent_memory")
		if isinstance(agent_memory_payload, dict):
			agent_memory_payload.pop("dialogue_log", None)

		retries = 0
		errors: List[str] = []
		feedback: Optional[str] = None
		attr_ids, attr_name_to_id = self._build_attribute_refs(agent_input.llm_input.available_attributes)
		available_attributes = sorted(set(attr_ids))
		char_ids = {char.id for char in agent_input.llm_input.valid_characters if char.id}

		llm_output: Optional[DmAgentLlmOutput] = None
		for _ in range(self.max_retries + 1):
			try:
				llm_output = self.llm_service.call_llm_json(
					agent_name="dmagent",
					system_prompt=DM_SYSTEM_PROMPT,
					user_payload=user_payload,
					output_model=DmAgentLlmOutput,
					retry_budget=0,
					validation_feedback=feedback,
				)
			except LLMValidationError as exc:
				retries += 1
				errors = [f"schema_error: {x.get('loc')} {x.get('msg')}" for x in exc.errors]
				feedback = " ; ".join(errors)
				continue

			errors = self._validate_semantics(
				llm_output=llm_output,
				actor_id=agent_input.llm_input.e1.source_id,
				available_attributes=available_attributes,
				valid_character_ids=char_ids,
				attribute_name_to_id=attr_name_to_id,
			)
			if not errors:
				break

			retries += 1
			feedback = self._build_validation_feedback(
				errors=errors,
				attribute_ids=attr_ids,
				attribute_name_to_id=attr_name_to_id,
				valid_character_ids=char_ids,
			)

		if llm_output is None:
			raise RuntimeError("DM agent produced no output")

		if errors:
			llm_output = DmAgentLlmOutput.model_validate(
				{
					"intent_info": {
						"intent": "blocked",
						"routing_hint": None,
						"attributes": [],
						"against_char_id": [],
						"difficulty": None,
						"dm_reply": "你的输入包含无效属性或目标，请重试。",
					}
				}
			)

		return DmAnalyzeResult(
			output=DmAgentOutput(
				llm_output=llm_output,
				system_output=DmAgentSystemOutput(e1_view=agent_input.llm_input.e1),
			),
			retries=retries,
			validation_errors=errors,
		)

	@classmethod
	def _precheck_guardrail(cls, agent_input: DmAgentInput) -> Optional[DmAgentLlmOutput]:
		raw_text = agent_input.llm_input.e1.raw_text or ""
		normalized_text = cls._normalize_guardrail_text(raw_text)
		context_text = cls._build_guardrail_context(agent_input)

		if not normalized_text:
			return cls._build_blocked_output(
				intent="blocked_low_signal_input",
				reply="这条输入缺少可执行的场景意图，请改成明确的观察、提问或行动。",
			)

		if cls._contains_any(normalized_text, _META_BLOCK_KEYWORDS):
			return cls._build_blocked_output(
				intent="blocked_meta_request",
				reply="这个请求超出当前游戏交互范围，请回到角色行动。",
			)

		if cls._contains_any(normalized_text, _ABUSIVE_KEYWORDS):
			return cls._build_blocked_output(
				intent="blocked_abusive_input",
				reply="请避免辱骂或攻击性表达，改用场景内、面向角色行动的表述。",
			)

		if cls._is_education_conflict(normalized_text):
			return cls._build_blocked_output(
				intent="blocked_education_conflict",
				reply="当前场景以历史、文学与文化理解为主，不支持低俗、猎奇或羞辱性内容。",
			)

		if cls._is_off_topic_request(normalized_text):
			return cls._build_blocked_output(
				intent="blocked_offtopic_request",
				reply="请回到当前故事世界中的角色行动、观察、提问或判断，不要切到现实助手任务。",
			)

		if cls._is_narrative_conflict(normalized_text, context_text):
			return cls._build_blocked_output(
				intent="blocked_narrative_conflict",
				reply="请遵守当前场景的设定、时代背景与已发生的叙事事实，在现有情境内行动。",
			)

		if cls._is_low_signal_input(raw_text, normalized_text):
			return cls._build_blocked_output(
				intent="blocked_low_signal_input",
				reply="这条输入缺少可执行的场景意图，请改成明确的观察、提问或行动。",
			)

		return None

	@staticmethod
	def _normalize_guardrail_text(text: str) -> str:
		return re.sub(r"\s+", "", text).lower()

	@classmethod
	def _build_guardrail_context(cls, agent_input: DmAgentInput) -> str:
		parts: List[str] = []
		world_info = agent_input.llm_input.world_info
		parts.append(world_info.map_name)
		parts.extend(world_info.map_description.public)
		if world_info.map_description.hint:
			parts.append(world_info.map_description.hint)
		parts.extend(item.content for item in world_info.map_description.add)

		for entity in world_info.characters.values():
			parts.append(entity.entity_name)
			parts.extend(entity.description.public)
			if entity.description.hint:
				parts.append(entity.description.hint)
			parts.extend(item.content for item in entity.description.add)

		for entity in world_info.items.values():
			parts.append(entity.entity_name)
			parts.extend(entity.description.public)
			if entity.description.hint:
				parts.append(entity.description.hint)
			parts.extend(item.content for item in entity.description.add)

		for entry in agent_input.llm_input.narrative_info.recent:
			parts.append(entry.content)

		return cls._normalize_guardrail_text(" ".join(parts))

	@staticmethod
	def _build_blocked_output(*, intent: str, reply: str) -> DmAgentLlmOutput:
		return DmAgentLlmOutput.model_validate(
			{
				"intent_info": {
					"intent": intent,
					"routing_hint": None,
					"attributes": [],
					"against_char_id": [],
					"difficulty": None,
					"dm_reply": reply,
				}
			}
		)

	@staticmethod
	def _contains_any(text: str, keywords: Tuple[str, ...]) -> bool:
		return any(keyword in text for keyword in keywords)

	@classmethod
	def _is_off_topic_request(cls, text: str) -> bool:
		if cls._contains_any(text, _OFF_TOPIC_REQUEST_PHRASES):
			return True
		return cls._contains_any(text, _OFF_TOPIC_HELP_VERBS) and cls._contains_any(text, _OFF_TOPIC_DOMAIN_KEYWORDS)

	@classmethod
	def _is_narrative_conflict(cls, text: str, context_text: str) -> bool:
		if cls._contains_any(text, _NARRATIVE_OVERRIDE_KEYWORDS):
			return True
		return any(keyword in text and keyword not in context_text for keyword in _MODERN_SETTING_KEYWORDS)

	@staticmethod
	def _is_education_conflict(text: str) -> bool:
		if any(keyword in text for keyword in _EDUCATION_CONFLICT_KEYWORDS):
			return True
		return any(prefix in text for prefix in _GRAPHIC_REQUEST_PREFIXES) and any(
			keyword in text for keyword in _GRAPHIC_REQUEST_KEYWORDS
		)

	@staticmethod
	def _is_low_signal_input(raw_text: str, normalized_text: str) -> bool:
		if not _ALNUM_OR_CJK_PATTERN.search(raw_text):
			return True
		return len(normalized_text) >= 6 and len(set(normalized_text)) == 1

	@staticmethod
	def _validate_semantics(
		*,
		llm_output: DmAgentLlmOutput,
		actor_id: str,
		available_attributes: List[str],
		valid_character_ids: Set[str],
		attribute_name_to_id: Optional[Dict[str, str]] = None,
	) -> List[str]:
		errors: List[str] = []
		intent = llm_output.intent_info

		if intent.routing_hint not in {None, "num", "against"}:
			errors.append("routing_hint must be null/num/against")

		attrs = intent.attributes or []
		ids = intent.against_char_id or []

		if intent.routing_hint is None:
			if attrs:
				errors.append("routing_hint is null but attributes is not empty")
			if ids:
				errors.append("routing_hint is null but against_char_id is not empty")
			return errors

		if not attrs:
			errors.append("check routing requires attributes")

		normalized_attrs: List[str] = []
		attribute_name_to_id = attribute_name_to_id or {}
		for attr in attrs:
			canonical_attr = attribute_name_to_id.get(attr, attr)
			if canonical_attr not in available_attributes:
				errors.append(f"invalid attribute: {attr}")
				continue
			normalized_attrs.append(canonical_attr)

		if normalized_attrs:
			intent.attributes = normalized_attrs

		for char_id in ids:
			if char_id not in valid_character_ids:
				errors.append(f"invalid char id: {char_id}")

		if intent.routing_hint == "num":
			if len(ids) > 1:
				errors.append("num routing should not include multiple against_char_id values")
			return errors

		if not ids:
			errors.append("against routing requires against_char_id")
		else:
			if len(ids) < 2:
				errors.append("against routing requires at least 2 character ids")
			if len(set(ids)) != len(ids):
				errors.append("against routing contains duplicate character ids")
			if actor_id and actor_id not in ids:
				errors.append("against routing must include actor id")
			if actor_id and ids and ids[0] != actor_id:
				errors.append("against routing requires actor id as first against_char_id")

		return errors

	@staticmethod
	def _build_attribute_refs(available_attributes: List[AvailableAttributeRef]) -> Tuple[List[str], Dict[str, str]]:
		attr_ids: List[str] = []
		name_to_id: Dict[str, str] = {}
		for attr in available_attributes:
			if not attr.id:
				continue
			attr_ids.append(attr.id)
			if attr.name:
				name_to_id[attr.name] = attr.id
		return attr_ids, name_to_id

	@staticmethod
	def _build_validation_feedback(
		*,
		errors: List[str],
		attribute_ids: List[str],
		attribute_name_to_id: Dict[str, str],
		valid_character_ids: Set[str],
	) -> str:
		parts = list(errors)
		if attribute_ids:
			parts.append(f"allowed attribute ids: {', '.join(attribute_ids)}")
		if attribute_name_to_id:
			name_pairs = [f"{name}->{attr_id}" for name, attr_id in sorted(attribute_name_to_id.items())]
			parts.append(f"attribute name to id mapping: {', '.join(name_pairs)}")
		if valid_character_ids:
			parts.append(f"valid character ids: {', '.join(sorted(valid_character_ids))}")
		parts.append("when you output attributes or against_char_id, you must return exact ids from the provided lists")
		return " ; ".join(parts)
