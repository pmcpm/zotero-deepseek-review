---
name: zotero-deepseek-review
description: >-
  Produce Chinese related-work sections and literature reviews through literature
  research, verified Zotero records, configurable model drafting (CSTCloud DeepSeek by default), and editable
  Zotero citations in Word. Use for end-to-end 文献调研、相关工作、文献综述写作
  with Zotero and a writing model, including user-supplied full texts and GB/T 7714 or
  APA bibliography requirements. For a single lookup or a standalone document
  formatting task, use the corresponding specialized skill.
---

# 文献调研与 Zotero 写作

Codex 负责需求、检索、证据卡、论证结构、题录核对、提示词和文档制作；配置的写作模型（默认 CSTCloud DeepSeek）负责全部论文正文及内容修正。交付保留 Zotero 原生引文域，支持用户继续使用 Word 的 Zotero 插件。

## 开始任务

1. 读取当前会话及已有任务状态，复用已明确的要求。按 [需求与文献](references/intake-and-research.md) 使用 ask clarification 询问缺失参数，尤其是字数、文献数量与中英文比例、引用格式、DOI 和双语著录。先核对具体工作范围，再准备写作。
2. 查找并读取当前环境的 `nature-academic-search`、`zotero:Zotero` 和 `documents:documents` skill；按阶段加载其支持资源。默认 CSTCloud 路径另加载 `cstcloud-model-api`；其他渠道按 [模型渠道](references/model-providers.md) 配置 OpenAI 兼容接口。复用用户已选渠道和模型，未指定时保持 CSTCloud / `deepseek-v4-flash`。普通检索跨期刊进行；用户明确要求 Nature/CNS 来源时再加载 `nature-citation`。
3. 运行 `scripts/workflow.py --help` 和 `scripts/bridge_client.py --help`，使用本 skill 当前脚本及其实际参数。任务数据契约见 [data-contract.md](references/data-contract.md)。通过 Zotero skill 探测本机连接，再按 [桥接与恢复](references/bridge-and-recovery.md) 探测健康状态及登记本次任务。文档工作使用 workspace dependency loader 返回的运行时。

工作流脚本依次提供 `init` / `check`、`assign-ids`、`metadata-audit`、`supplements`、`prompt`、`write`、`audit` / `review`。`write` 之后仍需 Word 实际字数和 Codex 语义审查；Word 阶段通过 `workflow.load_selected()` 校验已通过门槛的 `selected.json`、task/evidence 哈希及模型原文。模拟响应只供测试。

## 按证据推进

- 按主题检索、去重并核对题录，建立稳定的 `R001` 类 ID 和证据卡。导入前对照权威来源，导入后从 Zotero 读回核对；具体方法和发现以全文位置为依据。详见 [需求与文献](references/intake-and-research.md)。
- 获取受阻适用于任何来源和语言。寻找公开版本后，向用户给出待补题录、链接、建议文件名和目录，继续独立工作；依赖该全文的论证及写作等待补齐。保存待补清单和阶段状态，收到文件后继续原任务。
- 先形成研究路径比较、证据组合和章节逻辑，再按 [写作与核查](references/writing-and-quality.md) 生成动态提示词、调用写作模型、保存每轮结果并审查。内容问题交回同一写作模型，默认最多修正两轮。Codex 对论文正文只做只读审查。
- 按 [Word 与引文](references/word-and-citations.md) 制作文档，按 [CSL 契约](references/csl-contract.md) 配置引用样式。用户模板优先；所有文档文字为黑色，连接符统一使用 ASCII `-`。安装、任务 scope、请求恢复和后续 Refresh 的条件见 [桥接与恢复](references/bridge-and-recovery.md)。

## 执行约束

文末著录默认采用 `url_policy=resource_only`：期刊、会议、图书等正式出版物省略普通 HTTP/HTTPS 链接及引用日期；网页、在线公告、EB/OL 等确有在线定位需要的资源保留 URL 与所需发布日期、引用日期。DOI 显示仍按独立选项控制；双语英文著录执行相同规则，原始题录链接完整保留。

- 日常流程使用本地 API、受限桥接及任务自有 Word COM 实例。禁止 Computer Use、模拟鼠标键盘、SendKeys、剪贴板接管以及切换用户工作窗口来完成操作。首次安装、启用或正常授权对话框可由用户处理；后台路径失败时说明具体所需操作。
- 仅修改任务明确纳入的 Zotero 条目、分类和文件。先读取再合并，冲突时重读；有不确定结果的写请求先查询状态及实际结果，避免盲目重试。
- 保留模型正文、原始响应、提示词、证据卡和核查结果。只有引用 ID 转换和格式处理可以改变交付表示，最终正文与通过审查的模型响应保持可核对映射及哈希。
- 交付前核对字数、证据与引用、文末题录、编号、DOI/双语策略、原生域和版式；保存后重开并刷新。Word 原生导出 PDF 后逐页查看 PNG；渲染故障处理按 documents skill。

## 交付

交付 `.docx` 和简短核查结论，指出留存的 Zotero 分类及任务材料位置。文档保持 `CSL_CITATION`、`ZOTERO_BIBL` 域和正常更新设置。用户后续通过 Add/Edit Citation 增加或修改引用；删除、移动和题录变更可用 Refresh 统一更新。具体恢复条件见 [Word 与引文](references/word-and-citations.md)。

本机兼容基线、已运行测试和持续使用条件见 [验证记录](references/validation.md)。
