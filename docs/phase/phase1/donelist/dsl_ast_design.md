# 条件 DSL AST 设计文档（Phase 1）

## 目标
- 支持 `==`, `!=`, `>`, `<`, `>=`, `<=`, `in`, `not in`
- 支持逻辑组合 `and`, `or`, `not`
- 强制在同一世界快照版本下求值

## 词法 Token
- 比较操作符：`==`, `!=`, `>`, `<`, `>=`, `<=`
- 逻辑关键字：`and`, `or`, `not`
- 集合关键字：`in`, `not in`
- 括号：`(`, `)`
- 列表字面量：`[`, `]`, `,`
- 标识符：`char-player-0000.attributes.hp.value`、`item-key-0008.location`
- 字面量：字符串、数值、布尔、null

## 语法（EBNF）
```text
expr        := or_expr
or_expr     := and_expr ("or" and_expr)*
and_expr    := not_expr ("and" not_expr)*
not_expr    := "not" not_expr | cmp_expr
cmp_expr    := primary (("==" | "!=" | ">" | "<" | ">=" | "<=" | "in" | "not in") primary)?
primary     := identifier | literal | list_literal | "(" expr ")"
list_literal:= "[" [primary ("," primary)*] "]"
```

## AST 节点
- `literal(value)`：字面量
- `identifier(path)`：实体字段路径或字符串标识
- `list(children)`：列表字面量
- `cmp(op, left, right)`：比较节点
- `not(right)`：逻辑非
- `and(left, right)`：逻辑与
- `or(left, right)`：逻辑或

## 求值规则
1. 若传入 `expected_version`，先校验 `snapshot.version` 一致
2. 路径解析根节点限定为：`char-*` / `item-*` / `map-*`
3. 仅做只读求值，不产生写操作副作用
4. 顶层表达式必须返回布尔值

## 示例 AST
输入：
```text
char-player-0000.attributes.hp.value > 0 and item-key-0008.location == char-player-0000
```
输出（简化）：
```text
and(
  cmp(">", identifier("char-player-0000.attributes.hp.value"), literal(0)),
  cmp("==", identifier("item-key-0008.location"), identifier("char-player-0000"))
)
```
