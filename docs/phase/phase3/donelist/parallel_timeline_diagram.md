# Phase3 并发三分支时序图

```mermaid
sequenceDiagram
    participant E as EvolutionAgent
    participant S as NpcSchedulerBranch
    participant T as StatePatchBranch
    participant N as NarrativeBranch

    E->>S: dispatch(summary, turn_id=7, trace_id=7001)
    E->>T: dispatch(summary, turn_id=7, trace_id=7001)
    E->>N: dispatch(summary, turn_id=7, trace_id=7001)

    Note over S,T,N: 三个分支并发启动（时间戳见 parallel_timeline.log）

    S-->>E: scheduler output
    T-->>E: state commit result
    N-->>E: narrative draft
```

参照日志: `docs/phase/phase3/donelist/parallel_timeline.log`
