# 多形式讲解：原项目增量协议

保留现有 `lecture` 资源、Markdown `content`、数据库与具名 SSE 事件。
结构化演示只存入 `metadata.explanation_bundle`；旧讲解仍直接渲染 Markdown。

```json
{
  "schema_version": 1,
  "title": "具体主题",
  "summary": "图解要解释的关系",
  "nodes": [{"id": "a", "label": "具体对象", "detail": "对象的含义"}],
  "edges": [{"from": "a", "to": "b", "label": "关系"}],
  "steps": [{
    "title": "具体步骤",
    "caption": "此时为什么发生变化",
    "active_nodes": ["a"],
    "active_edges": [0],
    "duration": 4
  }]
}
```

- 最多 12 个节点、24 条边、8 个步骤；单步骤 2–12 秒。
- 节点 ID 唯一；边、步骤中的引用必须存在；所有文字有长度边界。
- 模型提供内容数据，前端以受控绘制器渲染，不执行模型代码。
- 同一数据用于图解、逐步播放、录制。视频只含画面和字幕，不含配音。
- 无合适可视化时可返回 `null`。结构修复失败显示 `visualization_error`，保留文字。
- 结构校验通过只证明可渲染，不代表已经核实全部知识事实。
- 此轮仍为逐步演示，不宣称支持任意物理仿真或参数实验。
