我打算继续完成agent_output.py,其中对于每个agent的输出包含两个部分一个部分属于系统一个部分属于llm应该完成的输出,
## dm_agent的输出如下:
- 首先是llm
E2IntentInfo来自agent_chain_input.py
- 然后是系统的输出
E1LlmView来自agent_input.py
## evolution_agent的输出如下:
- llm:
E4LlmView中的summary来自agent_input.py
- 系统:
无
## narrative_agent的输出如下:
- llm:
{
    narrative_str = str :叙事文本
}
- 系统:
{
    turn_id = int 
    trace_id  = int : 同上这里作为key消费,表示该叙事文本是在此调度轮次中产生的第几个,如玩家输入后会产生一个,同时这个回合之后的npc会产生新的一个于是第一个的id为1,第二个为2,依次类推
}
## merger_agent的输出如下:
- llm:
{
    narrative_str = str :叙事文本
}
- 系统:
无

## state_agent的输出如下:
- llm:
{
    变更列表:[
    {
        操作符 add/update/move/set
        操作符对应的字段 add对应可写入列表 update对应number move对应具有唯一真值性值(如location) set
        操作符对应的需要输入的值 
        ....
    }
    ]
    
}
- 系统:
```ts
PatchMeta {
  trace_id: int;
  turn_id: int;
  retry_seq: number;
}
## npc_shcduler_agent 调度器:
- llm
{
    E4LlmView中的来自agent_input.py

}
- 系统
{
    trace_id: int
    turn_id: int
}
## npc_performer的输出如下:
- llm
{
    raw_input: str npc输出的行为如对玩家的回复,对世界变化的反应等
    change_basic_goal: str 无填null,有就填
    change_activate_goal : str 无填null ,有就填
}
- 系统
{
    id: str 角色id
    trace_id: int
    turn_id: int
}

