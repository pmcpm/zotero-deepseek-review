# CSL 与双语著录接口

读取本文件的时机：准备 Word 引用样式、处理 DOI 显示策略、生成双语英文著录、或检查用户更换样式后的结果。

## 独立样式与元数据

运行 `scripts/csl_tools.py <base.csl> <output.csl> --task-id <id> [--hide-doi] [--bilingual]`。输入必须为含 citation 和 bibliography 的独立样式；依赖样式先解析其 parent。输出包含独立样式 ID 及同名 `.policy.json`，保留原始 CSL 的作者和许可信息。安装该副本，保持用户基础样式原样。GB/T 7714 的基础 ID 为 `china-national-standard-gb-t-7714-2015-numeric`，APA 为 `apa`。

先定位本机 Zotero 已安装样式；缺少时从 Zotero 官方样式库或 Citation Style Language 官方 styles 仓库取得对应版本，保存来源和内容指纹。其他期刊格式按用户提供的名称、样例或模板确定基础 CSL。

所有局部及通用 locale 的页码、年份、引用范围连接符统一为 ASCII `-`。隐藏 DOI 时，转换器去除 DOI 输出节点；运行时清除引用处理器接收到的 DOI 与 DOI 解析器 URL。Zotero 原始 DOI、URL、accessDate 和 Extra 保持完整。

新生成样式默认 `url_policy=resource_only`。常规期刊、会议、图书、章节、学位论文、专利及报告等条目省略普通 URL、`accessed` 及 Extra 中相应 URL/引用日期覆盖项；发布日期 `issued` 保留。`webpage`、`post`、`post-weblog`、`dataset`、`software` 和 genre/medium 明确标识 EB/OL 的资源保留 URL 与所需日期。其他在线公告或网络资源经 Codex 核对后，将其 Zotero key 放入 `policy.url_required_item_keys`，按条目保留链接。在线期刊 PDF、出版社落地页和机构库副本链接不构成默认保留的理由。

仅在用户或投稿模板明确要求时，使用 `--url-policy all` 覆盖默认值。DOI 字段及 DOI 解析链接仍由独立 `hide_doi` 选项决定。链接策略计入样式 ID 和内容指纹；变更后重新生成双语英文缓存。0.2.0 旧策略在历史任务中沿用原行为；新建文档须安装带明确 url_policy 的新版任务样式。

ASCII 展示规则也覆盖题名、刊名、页码、作者姓名等字段、英文附加著录和 CSL 的字面量。转换仅作用于渲染副本，源题录及英文证据保持完整；DOI、URL、URI、条目 ID 及展示文字内嵌的 DOI/URI token 保持原值。

纯 CSL 无法按正则识别 DOI URL。后续 Word Refresh 继续执行此策略需要本地桥接扩展保持启用。桥接仅适配已登记的自定义样式，其他 Zotero 样式沿用原实现。

任务样式先从原始题录的 CSL 映射取得 URL 和访问日期，再按文献类型执行普通 URL 与 DOI 策略；全局偏好保持原值。这使网络资源的必要链接完整，而正式出版物按任务策略省略链接。

## 本地扩展运行时

`assets/zotero-bridge/csl-runtime.js` 导出全局 `CodexReviewCSL`。扩展通过 `Services.scriptloader.loadSubScript(rootURI + 'csl-runtime.js', scope)` 加载。

- `installHooks(resolvePolicy)`：包装 `Zotero.Style.prototype.getCiteProc`，返回清理函数。`resolvePolicy(styleID)` 为登记样式返回策略，为其他样式返回 null。
- `createEngine(xml, locale, format, policy)`：使用 Zotero 的原生 citeproc-js 和文献读取器，调整文献的内存副本，保留作者解析设置；APA 的副标题大小写规则由 `base_style_id` 决定。
- `renderEnglish(englishStyleXML, englishCSL, policy)`：依据已核验英文题录返回 `{text, html, inline_html}`。文本包含单条完整英文参考文献，无序号；`inline_html` 去除块容器并以白名单重建行内标签，保留期刊、卷号等斜体。text 用于审查，inline_html 用于双语括号中的英文著录。
- `canonical(value)`：递归排序 JSON 对象属性的确定性序列化。
- `metadataSnapshot(data)`：对题录字段做确定性序列化，排除同步版本、修改时间、分类、标签、关系和子条目清单等非题录字段；保留作者、题名、刊名、访问日期与 Extra 等实际著录字段。用于英文著录缓存的 `item_snapshot`，与检测用户写入冲突的完整条目 snapshotToken 分开。

策略采用 `.policy.json` 字段，并可添加：

```json
{
  "style_id": "任务独立样式 ID",
  "base_style_id": "原始样式 ID",
  "style_fingerprint": "独立样式 XML 的 SHA256",
  "hide_doi": true,
  "url_policy": "resource_only",
  "url_required_item_keys": [],
  "bilingual": true,
  "english_entries": {
    "ZOTERO_ITEM_KEY": {
      "text": "依据当前格式生成的英文著录",
      "inline_html": "依据相同格式生成并净化的英文行内 HTML，保留 i/b/sub/sup 等",
      "style_fingerprint": "同一独立样式的 SHA256",
      "item_snapshot": "CodexReviewCSL.metadataSnapshot(item.toJSON())"
    }
  }
}
```

运行时将匹配样式和题录快照的英文条目作为 `annote` 注入渲染副本，CSL 在中文著录后附加括号。真实题录字段或样式改变时抑制旧英文条目，重新同步后再交付；单纯的同步版本、修改时间、分类或标签变化保留英文著录。此方法避免把 GB/T 英文字符串沿用到 APA，也避免英文著录长期占用用户 Extra 字段。

双语布局保留原中文著录的结尾标点，再附加括号英文著录；括号外不重复添加原 layout 的句号。英文缓存有 inline_html 时，运行时重新净化后注入 annote，使 citeproc 的 HTML/Word 输出保留英文斜体；仅有 text 的旧缓存继续输出纯文本，需重建英文缓存以恢复其样式。

## 双语内容的生成与变更

英文题录优先来自期刊、出版社、全文英文题名及作者英文名。必要译写须在证据侧记录原文、译写来源和核验状态；英文作者名及期刊名均需来源依据。Codex 核对，本任务写作模型执行需要的语言译写。对仅有中文名字的作者，先取得来源中的英文署名或请用户补充。

为英文著录另生成 `--english-render` 变体，可同时传入 `--hide-doi`，url_policy 与中文主样式一致。该变体去掉 annote、参考文献序号和悬挂标签设置，使用 en-US locale。对核验过的英文 CSL JSON 调用 `renderEnglish`，传入原条目 sourceKey 以同步逐条 URL 例外。必须将返回的 `text` 和 `inline_html` 两项一起写入 `english_entries[itemKey]`，再与当前中文独立样式的指纹、当前条目快照绑定。text 用于审查核对，inline_html 用于保留期刊及卷号斜体的实际渲染。每次输出前核对英文著录是否与正文所选样式及链接规则一致。

用户后续通过 Word 的 Zotero 插件新增和修改引用，原生编号继续更新。新中文条目的双语英文著录以及修改过的英文题录，在重新运行本 skill 的元数据同步阶段生成；用户改变引用样式后也需重建英文著录。原生编号更新与双语资料同步分别验收。
