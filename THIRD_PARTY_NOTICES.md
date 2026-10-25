# 第三方来源与许可

## Zotero

`skills/zotero-deepseek-review/assets/zotero-bridge/bootstrap.js` 的原生笔记转引文部分基于 Zotero `integration.js` 改编，原版权属于 Corporation for Digital Scholarship 及相关贡献者。

- 上游源码：https://github.com/zotero/zotero
- 上游集成源码：https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/integration.js
- 许可证：AGPL-3.0-or-later；根目录与桥接目录均保留完整许可文本。
- 本地改动包括任务范围、请求账本、题录操作、Word 集成入口和 CSL 展示策略，日期及说明保留在源码头部。

本仓库整体采用 AGPL-3.0-or-later。二进制 XPI 对应的完整构建源码位于本仓库，构建时无需私有代码或凭据。

## 外部依赖与研究资料

Zotero Desktop、Microsoft Word、Codex 及外部 skills 分别由其提供方分发，本仓库不包含其程序或 skill 源码。`cstcloud-model-api` 是默认模型路径的外部依赖，使用者在本机单独安装并配置凭据。

基础 CSL 样式由使用者从 Zotero 样式库或合法来源安装；派生样式保留来源及许可信息。本仓库测试中的最小 CSL 为测试夹具。论文全文、文献库、第三方题录、作者材料和模型响应不随源码分发。
