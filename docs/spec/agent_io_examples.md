# Agent 输入输出示例文档

本文档基于 `docs/spec/draft_spec.md` 和 `src/data/model/` 中的数据模型定义，详细说明各 Agent 的输入输出结构与示例。

---

## 目录

1. [系统概述](#系统概述)
2. [dm_agent](#1-dm_agent)
3. [evolution_agent](#2-evolution_agent)
4. [state_change_agent](#3-state_change_agent)
5. [npc_scheduler_agent](#4-npc_scheduler_agent)
6. [npc_performer_agent](#5-npc_performer_agent)
7. [narrative_agent](#6-narrative_agent)
8. [merger_agent](#7-merger_agent)
9. [附录：DSL 操作符说明](#附录dsl-操作符说明)

---

## 系统概述

### 链路信息说明

| 链路代号 | 名称 | 说明 |
|---------|------|------|
| E1 | 输入信息 | 输入系统产出的原始输入与路由元信息 |
| E2 | 意图诠释 | dm_agent 产生的输入语义理解 |
| E3 | 规则结算事实 | rule_system 产出的客观判断 |
| E4 | 步骤结算 | evolution_agent/scheduler 产生的推演结算 |
| E7 | 回合因果链 | 记录 narrative_agent 产生的叙事输出及因果关系 |

### Agent 分类

| Agent | 链路输入 | 类型 |
|-------|---------|------|
| dm_agent | E1 | 玩家交互入口 |
| evolution_agent | E1 + E3 + E7 | 推演结算 |
| state_change_agent | E4 + fallback_error | 状态变更 |
| npc_scheduler_agent | E4 | NPC 调度 |
| npc_performer_agent | E4(Scheduler) + E1 | NPC 执行 |
| narrative_agent | E4(Evolution) | 叙事生成 |
| merger_agent | E7 | 叙事合并 |

---

## 1. dm_agent

**职责**：与玩家进行对话，确定玩家意图，拦截非法输入，确定是否需要鉴定。

### 输入

```json
{
  "identity": {
    "id": "dmagent",
    "skill": "你是一个文字冒险游戏的导演代理，负责与玩家进行对话..."
  },
  "llm_input": {
    "e1": {
      "raw_text": "我想要撬开这个箱子",
      "source_id": "char-player-0000"
    },
    "world_info": {
      "map_id": "map-cellar-0001",
      "map_name": "酒窖",
      "map_description": {
        "public": ["阴暗潮湿的地窖", "墙壁上有水渍"],
        "hint": "地板上有可疑的划痕",
        "add": [{"turn": 5, "content": "空气中弥漫着霉味"}]
      },
      "characters": {
        "char-bandit-0002": {
          "entity_id": "char-bandit-0002",
          "entity_name": "强盗",
          "description": {
            "public": ["身穿破旧皮甲", "手持匕首"],
            "hint": "似乎在警惕着什么",
            "add": []
          }
        }
      },
      "items": {
        "item-wooden_chest-0003": {
          "entity_id": "item-wooden_chest-0003",
          "entity_name": "木箱",
          "description": {
            "public": ["一个上锁的木箱"],
            "hint": "锁已经被撬过多次",
            "add": []
          }
        }
      }
    },
    "narrative_info": {
      "recent": ["你进入了酒窖", "发现了木箱"],
      "narrative_log": [
        {"turn": 1, "content": "玩家进入酒窖"},
        {"turn": 2, "content": "玩家发现了木箱"}
      ]
    },
    "agent_memory": {
      "dialogue_log": [
        {"turn": 1, "content": "玩家：我想进入酒窖"},
        {"turn": 1, "content": "DM：酒窖入口阴暗潮湿"}
      ],
      "memory_turns": 5
    }
  },
  "system_input": {
    "chain_raw": {
      "e1": {
        "turn_id": 3,
        "trace_id": 1,
        "world_version": 5,
        "event_id": "evt-001",
        "source_id": "char-player-0000",
        "raw_text": "我想要撬开这个箱子"
      }
    },
    "execution": {
      "turn_id": 3,
      "trace_id": 1,
      "world_version": 5,
      "event_id": "evt-001",
      "debug": {},
      "turn_envelope": {
        "raw_input": "我想要撬开这个箱子",
        "turn": 3,
        "trace_id": 1,
        "debug": {}
      }
    }
  }
}
```

### 输出

```json
{
  "llm_output": {
    "intent_info": {
      "intent": "撬锁",
      "routing_hint": "num",
      "attributes": ["dexterity"],
      "against_char_id": null,
      "difficulty": "困难",
      "dm_reply": null
    }
  },
  "system_output": {
    "e1_view": {
      "raw_text": "我想要撬开这个箱子",
      "source_id": "char-player-0000"
    }
  }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|-----|------|------|
| intent | string | 玩家意图（如"撬锁"、"攻击"、"对话"） |
| routing_hint | string/null | 是否需要鉴定：`null` 不需要，`"num"` 数值鉴定，`"against"` 对抗鉴定 |
| attributes | List[string]/null | 需要鉴定的属性名称列表 |
| against_char_id | List[string]/null | 对抗鉴定对象的角色 ID 列表 |
| difficulty | string/null | 鉴定难度：`"简单"`、`"普通"`、`"困难"` |

---

## 2. evolution_agent

**职责**：利用鉴定信息和玩家原始输入以及世界、叙事信息推断接下来会发生什么，输出推演概要。

### 输入

```json
{
  "identity": {
    "id": "evolution",
    "skill": "你是世界演化代理，负责根据鉴定结果和输入推断状态变更..."
  },
  "llm_input": {
    "e1": {
      "raw_text": "我想要撬开这个箱子",
      "source_id": "char-player-0000"
    },
    "e3": {
      "intent": "撬锁",
      "success": "大成功"
    },
    "e7": {
      "narrative_causality": "玩家进入酒窖，发现木箱，尝试撬锁"
    },
    "world_info": {
      "map_id": "map-cellar-0001",
      "map_name": "酒窖",
      "map_description": {
        "public": ["阴暗潮湿的地窖", "墙壁上有水渍"],
        "hint": "地板上有可疑的划痕",
        "add": [{"turn": 5, "content": "空气中弥漫着霉味"}]
      },
      "characters": {
        "char-bandit-0002": {
          "entity_id": "char-bandit-0002",
          "entity_name": "强盗",
          "description": {
            "public": ["身穿破旧皮甲", "手持匕首"],
            "hint": "正在打盹",
            "add": []
          }
        }
      },
      "items": {
        "item-wooden_chest-0003": {
          "entity_id": "item-wooden_chest-0003",
          "entity_name": "木箱",
          "description": {
            "public": ["一个上锁的木箱"],
            "hint": "锁已经被撬过多次",
            "add": []
          }
        }
      }
    },
    "narrative_info": {
      "recent": ["你进入了酒窖", "发现了木箱"],
      "narrative_log": [
        {"turn": 1, "content": "玩家进入酒窖"},
        {"turn": 2, "content": "玩家发现了木箱"}
      ]
    }
  },
  "system_input": {
    "chain_raw": {
      "e1": {
        "turn_id": 3,
        "trace_id": 1,
        "world_version": 5,
        "event_id": "evt-001",
        "source_id": "char-player-0000",
        "raw_text": "我想要撬开这个箱子"
      },
      "e3": {
        "intent": "撬锁",
        "success": "大成功"
      },
      "e7": {
        "narrative_list": [
          {"trace_id": "1", "content": "玩家进入酒窖"},
          {"trace_id": "2", "content": "玩家发现了木箱"}
        ]
      }
    },
    "execution": {
      "turn_id": 3,
      "trace_id": 1,
      "world_version": 5,
      "event_id": "evt-001"
    }
  }
}
```

### 输出

```json
{
  "llm_output": {
    "summary": "玩家撬锁大成功，箱子被打开，里面有一把生锈的钥匙。强盗被声音惊醒，但因大成功未被发现。玩家获得钥匙，状态无变化。",
    "visible_to_player": true
  },
  "system_output": null
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|-----|------|------|
| summary | string | 推演概要，包含本回合发生了什么、状态变更 |
| visible_to_player | bool | 该变化是否对玩家可见，不可见则不经过 narrative_agent |

### E3 success 枚举值

| 值 | 说明 |
|---|------|
| `"大成功"` | 效果超出预期，可能获得额外收益 |
| `"成功"` | 动作按预期完成 |
| `"失败"` | 动作未成功，可能有负面后果 |
| `"大失败"` | 严重失败，可能导致危险状态 |

---

## 3. state_change_agent

**职责**：根据 evolution 输出的推演概要，利用状态管理操作符输出正确的状态变更信息。

### 输入

```json
{
  "identity": {
    "id": "state_change",
    "skill": "你是状态变更代理，负责根据推演概要生成状态变更操作..."
  },
  "llm_input": {
    "e4": {
      "summary": "玩家撬锁大成功，箱子被打开，里面有一把生锈的钥匙。强盗被声音惊醒，但因大成功未被发现。玩家获得钥匙，状态无变化。"
    },
    "world_info": {
      "map_id": "map-cellar-0001",
      "map_name": "酒窖",
      "entities": [
        {
          "entity_id": "char-player-0000",
          "entity_type": "character",
          "entity_name": "玩家",
          "description_summary": "勇敢的冒险者",
          "writable_fields": [
            {
              "field_path": "inventory",
              "field_name": "背包",
              "current_value": {},
              "value_type": "dict",
              "description": "玩家拥有的物品"
            },
            {
              "field_path": "attributes.strength.value",
              "field_name": "力量",
              "current_value": 70,
              "value_type": "number",
              "description": "力量属性当前值"
            }
          ]
        },
        {
          "entity_id": "item-wooden_chest-0003",
          "entity_type": "item",
          "entity_name": "木箱",
          "description_summary": "一个上锁的木箱",
          "writable_fields": [
            {
              "field_path": "location",
              "field_name": "位置",
              "current_value": "map-cellar-0001",
              "value_type": "string",
              "description": "物品当前位置"
            }
          ]
        },
        {
          "entity_id": "item-rusty_key-0008",
          "entity_type": "item",
          "entity_name": "生锈的钥匙",
          "description_summary": "一把古老的钥匙",
          "writable_fields": [
            {
              "field_path": "location",
              "field_name": "位置",
              "current_value": "item-wooden_chest-0003",
              "value_type": "string",
              "description": "钥匙目前在箱子内"
            }
          ]
        }
      ]
    },
    "fallback_error": null
  },
  "system_input": {
    "chain_raw": {
      "e4": {
        "summary": "玩家撬锁大成功，箱子被打开，里面有一把生锈的钥匙..."
      },
      "fallback_error": null
    },
    "retry_control": {
      "can_retry": true,
      "retry_budget": 3,
      "fallback_error": null
    },
    "execution": {
      "turn_id": 3,
      "trace_id": 1,
      "world_version": 5
    }
  }
}
```

### 输出

```json
{
  "llm_output": {
    "changes": [
      {
        "op": "MOVE",
        "target_path": "item-rusty_key-0008.location",
        "value": "char-player-0000",
        "reason": "玩家打开箱子获得钥匙"
      },
      {
        "op": "ADD",
        "target_path": "char-player-0000.description.add",
        "value": {"turn": 3, "content": "获得了一把生锈的钥匙"},
        "reason": "记录状态变更"
      }
    ]
  },
  "system_output": {
    "patch_meta": {
      "trace_id": 1,
      "turn_id": 3,
      "retry_seq": 0,
      "patch_id": "patch-003-001",
      "expected_version": 5
    }
  }
}
```

### 状态变更操作符

| 操作符 | 用途 | 示例 |
|-------|------|------|
| `ADD` | 向列表字段追加元素 | `ADD char-player-0000.inventory = ["item-key-0001"]` |
| `REMOVE` | 从列表字段移除元素 | `REMOVE char-player-0000.inventory = ["item-key-0001"]` |
| `SET` | 直接设置字段值 | `SET char-player-0000.status = "normal"` |
| `UPDATE` | 更新数值字段 | `UPDATE char-player-0000.attributes.health.value = 80` |
| `MOVE` | 变更 location | `MOVE item-key-0001.location = char-player-0000` |
| `ASSERT` | 提交前断言 | `ASSERT map-cellar-0001.connections[0].is_locked == false` |

### Fallback Error 示例（重试场景）

```json
{
  "llm_output": {
    "changes": [
      {
        "op": "MOVE",
        "target_path": "item-nonexistent-9999.location",
        "value": "char-player-0000",
        "reason": "测试无效实体"
      }
    ]
  },
  "system_output": {
    "patch_meta": {
      "trace_id": 1,
      "turn_id": 3,
      "retry_seq": 0,
      "patch_id": "patch-003-001"
    }
  }
}
```

系统返回错误：

```json
{
  "fallback_error": {
    "code": "ENTITY_NOT_FOUND",
    "message": "实体 item-nonexistent-9999 不存在",
    "retry_count": 1,
    "retriable": true,
    "rollback_applied": false,
    "details": {
      "target_path": "item-nonexistent-9999.location"
    }
  }
}
```

---

## 4. npc_scheduler_agent

**职责**：根据推演概要、叙事信息和世界信息确定哪些 NPC 需要被激活，为其提供额外上下文。

### 输入

```json
{
  "identity": {
    "id": "npc_scheduler",
    "skill": "你是 NPC 调度代理，负责决定哪些 NPC 在本回合需要激活..."
  },
  "llm_input": {
    "e4": {
      "summary": "玩家撬锁大成功，箱子被打开，里面有一把生锈的钥匙。强盗被声音惊醒，但因大成功未被发现。玩家获得钥匙，状态无变化。"
    },
    "world_info": {
      "current_map": {
        "map_id": "map-cellar-0001",
        "map_name": "酒窖",
        "description": {
          "public": ["阴暗潮湿的地窖"],
          "add": []
        },
        "connections": [
          {
            "id": "conn-001",
            "name": "楼梯",
            "direction": "up",
            "target_map_id": "map-hall-0002",
            "is_locked": false
          }
        ],
        "characters": [
          {
            "id": "char-bandit-0002",
            "name": "强盗",
            "basic_info": "守卫酒窖的强盗",
            "description": {
              "public": ["身穿破旧皮甲", "手持匕首"],
              "add": []
            }
          }
        ],
        "items": [
          {
            "id": "item-wooden_chest-0003",
            "name": "木箱",
            "description": {
              "public": ["一个上锁的木箱"],
              "add": []
            }
          }
        ]
      },
      "adjacent_maps": [
        {
          "map_id": "map-hall-0002",
          "map_name": "大厅",
          "description": {"public": ["宽敞的大厅"], "add": []},
          "connections": [],
          "characters": [],
          "items": []
        }
      ],
      "player_location": "map-cellar-0001"
    },
    "narrative_info": {
      "recent": ["你进入了酒窖", "发现了木箱", "成功撬开了箱子"],
      "narrative_log": [
        {"turn": 1, "content": "玩家进入酒窖"},
        {"turn": 2, "content": "玩家发现了木箱"},
        {"turn": 3, "content": "玩家成功撬开了箱子"}
      ]
    }
  },
  "system_input": {
    "chain_raw": {
      "e4": {
        "summary": "玩家撬锁大成功..."
      }
    },
    "execution": {
      "turn_id": 3,
      "trace_id": 1
    }
  }
}
```

### 输出

```json
{
  "llm_output": {
    "step_result": {
      "summary": "玩家撬锁大成功，箱子被打开，里面有一把生锈的钥匙。强盗被声音惊醒，但因大成功未被发现。玩家获得钥匙，状态无变化。",
      "extra_npc_context": {
        "char-bandit-0002": "虽然你被声音惊动，但由于对方动作极其娴熟，你并未发现异常。继续保持警惕。"
      }
    }
  },
  "system_output": {
    "trace_id": 1,
    "turn_id": 3
  }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|-----|------|------|
| extra_npc_context | Dict[string, string/null] | 给 performer 的额外上下文，key 为 NPC ID，value 为上下文信息（无则为 null） |

---

## 5. npc_performer_agent

**职责**：根据 scheduler 提供的调度信息和额外上下文，确定 NPC 的互动类型和输出。

### 输入

```json
{
  "identity": {
    "id": "npc_performer",
    "skill": "你是 NPC 执行代理，负责根据调度计划执行 NPC 行为..."
  },
  "llm_input": {
    "e4": {
      "extra_npc_context": {
        "char-bandit-0002": "虽然你被声音惊动，但由于对方动作极其娴熟，你并未发现异常。继续保持警惕。"
      }
    },
    "e1": {
      "raw_text": "玩家撬开了箱子，获得钥匙",
      "source_id": "char-player-0000"
    },
    "world_info": {
      "id": "char-bandit-0002",
      "name": "强盗",
      "basic_info": "守卫酒窖的强盗，身穿破旧皮甲，手持匕首",
      "location": "map-cellar-0001",
      "description": {
        "public": ["身穿破旧皮甲", "手持匕首", "正在打盹"],
        "add": []
      }
    },
    "agent_memory": {
      "log": [
        {"turn": 1, "content": "强盗在酒窖巡逻"},
        {"turn": 2, "content": "强盗开始打盹"}
      ],
      "long_term_memory": [],
      "current_event": "虽然你被声音惊动，但由于对方动作极其娴熟，你并未发现异常。继续保持警惕。",
      "short": ["强盗被声音惊动"],
      "short_log": [
        {"turn": 2, "event": "强盗开始打盹"},
        {"turn": 3, "event": "强盗被声音惊醒"}
      ],
      "key_facts": ["强盗在酒窖担任守卫", "强盗手持武器"]
    }
  },
  "system_input": {
    "chain_raw": {
      "e4": {
        "extra_npc_context": {
          "char-bandit-0002": "虽然你被声音惊动..."
        }
      },
      "e1": {
        "turn_id": 3,
        "trace_id": 1,
        "source_id": "char-player-0000",
        "raw_text": "玩家撬开了箱子，获得钥匙"
      }
    },
    "execution": {
      "turn_id": 3,
      "trace_id": 1
    }
  }
}
```

### 输出

```json
{
  "llm_output": {
    "intent": "description",
    "action_text": "强盗微微睁开眼睛，警觉地扫视了一圈酒窖。由于玩家动作极为娴熟，强盗并未发现异常，嘟囔了一句后继续打盹。",
    "change_basic_goal": null,
    "change_active_goal": "保持警惕，注意酒窖中的异常声响"
  },
  "system_output": {
    "trace_id": 1,
    "turn_id": 3,
    "id": "char-bandit-0002"
  }
}
```

### NPC 互动类型

| 类型 | 说明 | 是否需要鉴定 |
|-----|------|-------------|
| `interaction` | NPC 与环境/物体互动 | 可能需要（高风险行为） |
| `dialogue` | NPC 与玩家/其他 NPC 对话 | 可能需要（影响剧情） |
| `description` | NPC 的非交互且非对话的描述性举动 | 不需要 |

---

## 6. narrative_agent

**职责**：根据推演概要和世界、叙事信息生成叙事片段。

### 输入

```json
{
  "identity": {
    "id": "narrative",
    "skill": "你是叙事代理，负责生成自然语言叙事片段..."
  },
  "llm_input": {
    "e4": {
      "summary": "玩家撬锁大成功，箱子被打开，里面有一把生锈的钥匙。强盗被声音惊醒，但因大成功未被发现。玩家获得钥匙，状态无变化。"
    },
    "world_info": {
      "map_slice": {
        "map_id": "map-cellar-0001",
        "map_name": "酒窖",
        "description": {
          "public": ["阴暗潮湿的地窖", "墙壁上有水渍"],
          "add": [{"turn": 5, "content": "空气中弥漫着霉味"}]
        },
        "connections": [
          {
            "id": "conn-001",
            "name": "楼梯",
            "direction": "up",
            "target_map_id": "map-hall-0002",
            "is_locked": false
          }
        ],
        "characters": [
          {
            "id": "char-bandit-0002",
            "name": "强盗",
            "basic_info": "守卫酒窖的强盗",
            "description": {
              "public": ["身穿破旧皮甲", "手持匕首"],
              "add": []
            }
          }
        ],
        "items": [
          {
            "id": "item-wooden_chest-0003",
            "name": "木箱",
            "description": {
              "public": ["一个上锁的木箱"],
              "add": []
            }
          },
          {
            "id": "item-rusty_key-0008",
            "name": "生锈的钥匙",
            "description": {
              "public": ["一把古老的钥匙"],
              "add": []
            }
          }
        ]
      }
    },
    "narrative_info": {
      "recent": ["你进入了酒窖", "发现了木箱"],
      "narrative_log": [
        {"turn": 1, "content": "玩家进入酒窖"},
        {"turn": 2, "content": "玩家发现了木箱"}
      ]
    }
  },
  "system_input": {
    "chain_raw": {
      "e4": {
        "summary": "玩家撬锁大成功，箱子被打开，里面有一把生锈的钥匙..."
      }
    },
    "execution": {
      "turn_id": 3,
      "trace_id": 1
    }
  }
}
```

### 输出

```json
{
  "llm_output": {
    "narrative_str": "你小心翼翼地将撬棍插入锁孔，凭借着敏捷的手指和多年的经验，你几乎没有发出任何声响。锁簧发出一声轻微的咔嗒声，箱子应声而开。\n\n借着昏暗的光线，你发现箱底躺着一把生锈的钥匙，正当你伸手去取时，旁边打盹的强盗似乎动了动——但他很快又恢复了沉睡。你屏住呼吸，一动不动，心中庆幸自己方才的娴熟手法。\n\n钥匙入手，冰凉而沉重，上面布满了岁月的痕迹。"
  },
  "system_output": {
    "trace_id": 1,
    "turn_id": 3
  }
}
```

---

## 7. merger_agent

**职责**：利用叙事信息和回合因果链输出简短的回合概要，加入到叙事信息中。

### 输入

```json
{
  "identity": {
    "id": "merger",
    "skill": "你是合并代理，负责将叙事片段合并为简短的回合概要..."
  },
  "llm_input": {
    "e7": {
      "narrative_causality": "玩家进入酒窖，发现木箱，成功撬开箱子并获得钥匙，强盗被惊动但未发现玩家"
    },
    "world_info": {
      "map_slice": {
        "map_id": "map-cellar-0001",
        "map_name": "酒窖",
        "description": {
          "public": ["阴暗潮湿的地窖"],
          "add": []
        },
        "connections": [],
        "characters": [
          {
            "id": "char-bandit-0002",
            "name": "强盗",
            "basic_info": "守卫酒窖的强盗",
            "description": {
              "public": ["身穿破旧皮甲", "手持匕首"],
              "add": []
            }
          }
        ],
        "items": [
          {
            "id": "item-wooden_chest-0003",
            "name": "木箱",
            "description": {
              "public": ["一个打开的木箱"],
              "add": []
            }
          }
        ]
      }
    },
    "narrative_info": {
      "recent": ["你进入了酒窖", "发现了木箱"],
      "narrative_log": [
        {"turn": 1, "content": "玩家进入酒窖"},
        {"turn": 2, "content": "玩家发现了木箱"},
        {"turn": 3, "content": "你小心翼翼地将撬棍插入锁孔..."}
      ]
    }
  },
  "system_input": {
    "chain_raw": {
      "e7": {
        "narrative_list": [
          {"trace_id": "1", "content": "玩家进入酒窖"},
          {"trace_id": "2", "content": "玩家发现了木箱"},
          {"trace_id": "3", "content": "你小心翼翼地将撬棍插入锁孔..."}
        ]
      }
    },
    "execution": {
      "turn_id": 3,
      "trace_id": 1
    }
  }
}
```

### 输出

```json
{
  "llm_output": {
    "narrative_str": "在酒窖中，玩家成功撬开木箱，获得一把生锈的钥匙。守卫的强盗被惊动但未发现异常。"
  },
  "system_output": null
}
```

---

## 附录：DSL 操作符说明

### 语法格式

```text
ASSERT [条件表达式]
ADD [实体ID].[字段路径] = [值1, 值2, ...]
REMOVE [实体ID].[字段路径] = [值1, 值2, ...]
SET [实体ID].[字段路径] = [新值]
UPDATE [实体ID].[字段路径] = [新值]
MOVE [实体ID].location = [目标实体ID]
```

### 操作符优先级

单次 `StatePatch` 的执行顺序固定为：

1. 解析补丁
2. 执行全部 `ASSERT`
3. 执行 `MOVE`
4. 执行 `SET` / `UPDATE`
5. 执行 `ADD` / `REMOVE`
6. 重新计算派生索引
7. 持久化提交

### 示例

```text
ASSERT map-cellar-0001.connections[0].is_locked == false
UPDATE char-player-0000.attributes.health.value = 80
MOVE item-room_key-0008.location = char-player-0000
ADD map-cellar-0001.description.add = [{"turn": 12, "content": "地板上多了被拖拽的痕迹"}]
REMOVE char-bandit-0002.extensions.combat.tags = ["hidden"]
SET map-cellar-0001.connections[0].is_locked = true
```

### 错误类型

| 错误码 | 说明 |
|-------|------|
| `ENTITY_NOT_FOUND` | 实体不存在 |
| `FIELD_NOT_FOUND` | 字段不存在 |
| `FIELD_NOT_MUTABLE` | 字段不可写 |
| `FIELD_TYPE_MISMATCH` | 字段类型不匹配 |
| `VALUE_OUT_OF_RANGE` | 数值超界 |
| `DUPLICATE_ENTRY` | 列表追加重复元素 |
| `ENTRY_NOT_FOUND` | 列表删除目标不存在 |
| `INVALID_TARGET` | MOVE 目标无效 |

---

## 实体 ID 命名规范

根据规范，实体 ID 必须满足：

```
[实体类型]-[有意义名称]-[4位递增数字]
```

| 实体类型 | 示例 |
|---------|------|
| `map-` | `map-cellar-0001`, `map-hall-0002` |
| `char-` | `char-player-0000`, `char-bandit-0002` |
| `item-` | `item-wooden_chest-0003`, `item-rusty_key-0008` |

**特殊规定**：
- 玩家实体固定 ID：`char-player-0000`
- 所有实体 ID 必须全局唯一，创建后不可修改
- 删除的实体 ID 归档，禁止复用
