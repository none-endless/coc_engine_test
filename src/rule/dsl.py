from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.data.model.world_state import WorldSnapshot


class DslError(ValueError):
    pass


@dataclass
class Token:
    kind: str
    value: str


@dataclass
class AstNode:
    kind: str
    value: Any = None
    left: Optional["AstNode"] = None
    right: Optional["AstNode"] = None
    children: Optional[List["AstNode"]] = None


_TOKEN_RE = re.compile(
    r"\s*(?:(>=|<=|==|!=|>|<)|\b(not\s+in|in|and|or|not)\b|(\()|(\))|(\[)|(\])|(,)|('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")|(-?\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_\-\.\[\]]*))",
    flags=re.IGNORECASE,
)


class _Tokenizer:
    def __init__(self, text: str) -> None:
        self.text = text

    def tokenize(self) -> List[Token]:
        pos = 0
        tokens: List[Token] = []
        while pos < len(self.text):
            match = _TOKEN_RE.match(self.text, pos)
            if not match:
                raise DslError(f"invalid token near: {self.text[pos:pos + 20]}")

            op, kw, lpar, rpar, lbr, rbr, comma, quoted, number, ident = match.groups()
            pos = match.end()

            if op:
                tokens.append(Token("OP", op))
            elif kw:
                kw_normalized = re.sub(r"\s+", " ", kw.lower())
                tokens.append(Token("KW", kw_normalized))
            elif lpar:
                tokens.append(Token("LPAR", lpar))
            elif rpar:
                tokens.append(Token("RPAR", rpar))
            elif lbr:
                tokens.append(Token("LBR", lbr))
            elif rbr:
                tokens.append(Token("RBR", rbr))
            elif comma:
                tokens.append(Token("COMMA", comma))
            elif quoted:
                tokens.append(Token("STRING", quoted[1:-1]))
            elif number:
                tokens.append(Token("NUMBER", number))
            elif ident:
                lowered = ident.lower()
                if lowered == "true":
                    tokens.append(Token("BOOL", "true"))
                elif lowered == "false":
                    tokens.append(Token("BOOL", "false"))
                elif lowered == "none" or lowered == "null":
                    tokens.append(Token("NULL", "null"))
                else:
                    tokens.append(Token("IDENT", ident))

        tokens.append(Token("EOF", ""))
        return tokens


class _Parser:
    def __init__(self, tokens: Sequence[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def parse(self) -> AstNode:
        node = self._parse_or()
        self._expect("EOF")
        return node

    def _peek(self) -> Token:
        return self.tokens[self.pos]

    def _advance(self) -> Token:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def _expect(self, kind: str, value: Optional[str] = None) -> Token:
        token = self._peek()
        if token.kind != kind:
            raise DslError(f"expect {kind}, got {token.kind}")
        if value is not None and token.value.lower() != value.lower():
            raise DslError(f"expect {value}, got {token.value}")
        return self._advance()

    def _match_kw(self, value: str) -> bool:
        token = self._peek()
        if token.kind == "KW" and token.value == value:
            self._advance()
            return True
        return False

    def _parse_or(self) -> AstNode:
        node = self._parse_and()
        while self._match_kw("or"):
            right = self._parse_and()
            node = AstNode(kind="or", left=node, right=right)
        return node

    def _parse_and(self) -> AstNode:
        node = self._parse_not()
        while self._match_kw("and"):
            right = self._parse_not()
            node = AstNode(kind="and", left=node, right=right)
        return node

    def _parse_not(self) -> AstNode:
        if self._match_kw("not"):
            return AstNode(kind="not", right=self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> AstNode:
        left = self._parse_primary()
        token = self._peek()

        if token.kind == "OP":
            op = self._advance().value
            right = self._parse_primary()
            return AstNode(kind="cmp", value=op, left=left, right=right)

        if token.kind == "KW" and token.value in {"in", "not in"}:
            op = self._advance().value
            right = self._parse_primary()
            return AstNode(kind="cmp", value=op, left=left, right=right)

        return left

    def _parse_primary(self) -> AstNode:
        token = self._peek()

        if token.kind == "LPAR":
            self._advance()
            node = self._parse_or()
            self._expect("RPAR")
            return node

        if token.kind == "LBR":
            return self._parse_list()

        if token.kind == "STRING":
            return AstNode(kind="literal", value=self._advance().value)

        if token.kind == "NUMBER":
            raw = self._advance().value
            if "." in raw:
                return AstNode(kind="literal", value=float(raw))
            return AstNode(kind="literal", value=int(raw))

        if token.kind == "BOOL":
            return AstNode(kind="literal", value=token.value == "true")

        if token.kind == "NULL":
            self._advance()
            return AstNode(kind="literal", value=None)

        if token.kind == "IDENT":
            ident = self._advance().value
            return AstNode(kind="identifier", value=ident)

        raise DslError(f"unexpected token: {token.kind} {token.value}")

    def _parse_list(self) -> AstNode:
        self._expect("LBR")
        items: List[AstNode] = []
        if self._peek().kind != "RBR":
            items.append(self._parse_primary())
            while self._peek().kind == "COMMA":
                self._advance()
                items.append(self._parse_primary())
        self._expect("RBR")
        return AstNode(kind="list", children=items)


class DslEngine:
    """Parse and evaluate read-only condition DSL on one snapshot version."""

    def parse(self, expression: str) -> AstNode:
        tokens = _Tokenizer(expression).tokenize()
        return _Parser(tokens).parse()

    def evaluate(
        self,
        expression: str,
        snapshot: WorldSnapshot | Dict[str, Any],
        expected_version: Optional[int] = None,
    ) -> bool:
        if expected_version is not None:
            version = snapshot.version if isinstance(snapshot, WorldSnapshot) else snapshot.get("version")
            if version != expected_version:
                raise DslError(f"snapshot version mismatch: expected {expected_version}, got {version}")

        ast = self.parse(expression)
        value = self._eval_node(ast, snapshot)
        if not isinstance(value, bool):
            raise DslError("expression must evaluate to boolean")
        return value

    def _eval_node(self, node: AstNode, snapshot: WorldSnapshot | Dict[str, Any]) -> Any:
        if node.kind == "literal":
            return node.value

        if node.kind == "identifier":
            return self._resolve_identifier(node.value, snapshot)

        if node.kind == "list":
            assert node.children is not None
            return [self._eval_node(child, snapshot) for child in node.children]

        if node.kind == "and":
            return bool(self._eval_node(node.left, snapshot) and self._eval_node(node.right, snapshot))

        if node.kind == "or":
            return bool(self._eval_node(node.left, snapshot) or self._eval_node(node.right, snapshot))

        if node.kind == "not":
            return not bool(self._eval_node(node.right, snapshot))

        if node.kind == "cmp":
            left = self._eval_node(node.left, snapshot)
            right = self._eval_node(node.right, snapshot)
            return self._compare(left, right, node.value)

        raise DslError(f"unsupported ast node: {node.kind}")

    def _resolve_identifier(self, text: str, snapshot: WorldSnapshot | Dict[str, Any]) -> Any:
        if "." not in text:
            return text

        root_entity_id, path_suffix = text.split(".", 1)

        if isinstance(snapshot, WorldSnapshot):
            if root_entity_id.startswith("char-"):
                data = snapshot.characters.get(root_entity_id)
            elif root_entity_id.startswith("item-"):
                data = snapshot.items.get(root_entity_id)
            elif root_entity_id.startswith("map-"):
                data = snapshot.maps.get(root_entity_id)
            else:
                return text
        else:
            if root_entity_id.startswith("char-"):
                data = snapshot.get("characters", {}).get(root_entity_id)
            elif root_entity_id.startswith("item-"):
                data = snapshot.get("items", {}).get(root_entity_id)
            elif root_entity_id.startswith("map-"):
                data = snapshot.get("maps", {}).get(root_entity_id)
            else:
                return text

        if data is None:
            raise DslError(f"entity not found: {root_entity_id}")

        return self._resolve_path(data, path_suffix)

    def _resolve_path(self, data: Any, path: str) -> Any:
        current = data
        for part in path.split("."):
            field, index = self._parse_index(part)

            if isinstance(current, dict):
                if field not in current:
                    raise DslError(f"field not found: {field}")
                current = current[field]
            else:
                if not hasattr(current, field):
                    raise DslError(f"field not found: {field}")
                current = getattr(current, field)

            if index is not None:
                if not isinstance(current, list):
                    raise DslError(f"field is not list: {field}")
                if index < 0 or index >= len(current):
                    raise DslError(f"list index out of range: {field}[{index}]")
                current = current[index]

        return current

    @staticmethod
    def _parse_index(part: str) -> Tuple[str, Optional[int]]:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]", part)
        if not match:
            return part, None
        return match.group(1), int(match.group(2))

    @staticmethod
    def _compare(left: Any, right: Any, op: str) -> bool:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
        if op == "in":
            return left in right
        if op == "not in":
            return left not in right
        raise DslError(f"unsupported operator: {op}")
