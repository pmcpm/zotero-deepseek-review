# 桥接接口与恢复

读取时机：首次准备环境、登记任务、修改 Zotero、安装任务 CSL、调用原生 Word 引文，或恢复失败请求。

命令示例中的 `scripts/` 相对本 skill 根目录解析；实际运行使用 workspace dependency loader 返回的 Python 与 PowerShell 支持环境。尖括号表示本次任务的真实路径或 ID。

## 首次安装与连接

扩展源码在 `assets/zotero-bridge/`。用 `scripts/package_bridge.py --output <目标.xpi>` 构建不含凭据的安装包。当前清单版本为 0.2.1，兼容边界为 Zotero 9.0.6-9.0.*；实际测试结果以本次验证记录为准。其他版本先在独立副本探测兼容性，验证后再扩大边界。

用户通过 Zotero“工具 → 插件 → 齿轮 → 从文件安装插件”安装，并处理首次启用或正常授权提示。相同扩展 ID 的旧验证版本应正常升级，避免两个桥接同时占用接口。日常任务直接连接已启用的扩展；安装失败先核对清单中的 id、update_url、strict_min_version 和 strict_max_version。禁止通过改写 userDisabled 等状态跳过启用过程。

启动后扩展在当前 Zotero profile 的 `codex-literature-bridge/config.json` 生成随机令牌，在同目录 `state.json` 保存任务、请求账本及任务 CSL 策略。客户端自动读取默认 profile；多 profile 或默认项不明确时用 `--profile <绝对路径>` 明确指定。

```powershell
python scripts/bridge_client.py --profile <profile路径> health
```

接口为 `POST http://127.0.0.1:23119/codex-bridge/v1`，JSON 请求，凭据只通过 `Authorization: Bearer ...` 请求头发送。客户端不打印令牌，不在命令参数、任务 JSON、安装包或公开日志中写入令牌；使用不经代理的回环连接。健康检查返回版本、Zotero 版本、busy 和 capabilities，完成操作还需核对自己的请求结果。

## 任务范围与题录操作

先完成 `workflow.py init/check` 的需求确认及稳定 ID 分配，再登记任务：

```powershell
python scripts/bridge_client.py register --task <task.json> --evidence <evidence.json> --output <登记结果.json>
```

输出目录先建立。登记限定 taskID、taskRoot、libraryID、collection、allowedItemKeys、attachmentRoots 和 documents。附件必须位于明确登记的目录，文档必须匹配登记的 DOCX 绝对路径。已有条目来源于 evidence 中的 Zotero key；本任务创建的条目和笔记由桥接纳入范围。已有条目另用 linkItem 加入分类。登记结果会把实际 collection_key 写回 task.json，因此在生成写作输入快照前完成登记。

同一 taskID 的后续登记保留之前授权的条目并追加新 key；需要缩小范围或改换任务目录/文献库时使用新的 taskID。避免用宽泛的磁盘根目录代替任务目录。附件目录可与任务目录分离，但须属于用户已确认的来源目录。

固定 op 与必要输入：

| op | 输入与用途 |
|---|---|
| getItem | taskID、key；返回 data、uri、csl 和不透明 snapshotToken |
| createItem | taskID、itemType、fields、metadataChecked=true；只导入已核实字段 |
| updateItem | taskID、key、fields、metadataChecked=true、expectedSnapshotToken |
| linkItem | taskID、key、expectedSnapshotToken；加入本任务分类 |
| attachPDF | taskID、key、path；为该条目导入登记目录内 PDF |
| createNote | taskID、html；创建带稳定 URI 引用的任务笔记 |
| installStyle | taskID、path、policy；安装任务目录中的独立 CSL 并登记渲染策略 |
| renderEnglish | taskID、styleID、englishCSL，可选 xml、sourceKey；同步逐条 URL 例外并渲染英文著录 |
| renderBibliography | taskID、styleID、keys，可选 format=text/html；原生引擎只读核对已授权条目的文末著录 |
| configureDocument | taskID、documentPath、styleID，可选 locale；初始化空文档偏好 |
| insertNote | taskID、documentPath、noteKey；原生转换笔记引用 |
| addBibliography | taskID、documentPath；生成原生列表，已有列表时返回 alreadyPresent |
| refreshDocument | taskID、documentPath；调用原生刷新 |

除 health/status 外，请求均带持久化 requestID。getItem 的快照令牌用于检测写入冲突，避免用同步版本号代替。获取实际 data 后与证据中的已核实题录比较，提交修改前先合并用户字段。收集 Zotero 读回项为 `{key,data}` 数组，再提交 `workflow.py metadata-audit ... --readback ...`；声明附件时包含其 parent key。创建、修改或重新登记实际分类后重做相应审查。

## 请求提交与恢复

请求放入任务目录的 JSON 文件，使用：

```powershell
python scripts/bridge_client.py call --request <请求.json> --output <响应.json>
```

CLI 在缺失 requestID 时先生成并写回请求文件，再发送。再次提交同一 ID 和同一内容会返回账本结果；同一 ID 改动 payload 返回冲突。扩展串行执行任务写操作，并在副作用前记录 started。请求账本和任务策略在 Zotero 重启后继续保存。

| 返回或故障 | 处理 |
|---|---|
| completed | 核对 result.ok，再读回条目、附件或文档域确认实际结果 |
| failed | 保存原请求与错误，检查是否已产生副作用；修正根因并核对数据后再决定新请求 |
| started / 超时 | 用 status 查询原 ID，再核对库和文档；保持原请求，避免重放插入 |
| unknown | 核对 profile、taskID、请求文件和实际结果；确认请求尚未执行后再发送保存的请求 |
| HTTP 409 busy | 等待当前命令结束，再重试同一保存请求，避免启动并行 Word 操作 |
| HTTP 409 payload conflict | 查出原 payload；需要不同操作时保存新的请求 ID |
| snapshot_conflict | 重读最新快照并合并有来源的修正，使用新的请求 ID 和最新令牌 |

status 请求为 `{ "op":"status", "requestID":"原请求ID" }`，仅查询。任意请求返回失败或连接断开后，先确认实际结果再恢复；端到端文档制作还应核对已保存副本。

## Word 运行条件

`word_document.ps1` 在制作前调用 `prepare_note.py`，后者通过 `workflow.load_selected()` 核对当前 task/evidence、不可变模型响应、精确字数、语义审查和输出许可，并从 Zotero 再读回题录。现有输出文件会被保护，重新生成时选择新的输出路径并相应登记；正文或证据变化需要重新通过写作审查。

Word 用法：

```powershell
& scripts/word_document.ps1 -TaskDir <任务目录> -PythonPath <bundled-python.exe> -StyleID <已安装任务CSL的完整ID> [-ProfilePath <profile路径>] [-TemplatePath <空白格式模板>]
```

模板应只提供格式，保持正文为空且无引文域；已有稿件的局部编辑另行限定范围。脚本初始化独立会话、设置 Field 和即时更新，创建原生笔记引文、生成列表、保存重开并刷新。其输出为目标 DOCX、任务内的 word 请求/响应、document-audit.json 和 preview.pdf。PDF 转为 PNG 后逐页核验，正文一致性和原生域检查通过后交付。

后台只操作任务自有 Word 实例和明确文档，不接管鼠标、键盘、剪贴板或用户窗口。发现实例已有文档时退出该操作并保留用户文档。扩展临时抑制本次命令结束时的文档激活，随后恢复原标志，保持用户后续插件行为。需要正常对话框时向用户说明并等待其处理。

## 交付后 Refresh 与 CSL hook

任务自定义 CSL 的 DOI URL 过滤和双语附加条目由桥接的 citeproc hook 执行，扩展需要保持启用；普通 Zotero 样式沿用原引擎。完整样式及英文渲染数据契约见 [csl-contract.md](csl-contract.md)。hook 只修改渲染时的元数据副本，Zotero 的 DOI、URL 和 Extra 保持完整。

英文著录缓存同时绑定独立样式内容指纹和实际著录字段快照。题录或样式变化时旧英文条目被抑制，运行本 skill 的元数据同步后重新生成；分类、标签和同步版本变化不会单独使英文条目失效。用户手动加入新中文文献后，原生编号照常更新，双语英文著录通过同步流程补齐。用户切换引用样式后也要重建相应英文条目。

交付保留原生引文和文末列表域。用户通过 Word Zotero Add/Edit Citation 新增或修改引用，删除、移动或题录更正后用 Refresh 统一更新。原生编号更新、DOI 策略和双语资料同步分别核验；只将实际运行过的场景记为通过。
