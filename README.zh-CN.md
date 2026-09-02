# HKUST(GZ) 教授信息

[English](README.md) | [简体中文](README.zh-CN.md)

本仓库维护经过审核和机器校验的 HKUST(GZ) 当前 Faculty Profiles 人员公开职业信息目录，并记录在指定更新窗口内通过 OpenAlex 与 `paperscraper` 检索、再由独立 arXiv 流程核验的论文。

论文部分并不穷尽。“本轮未检索到论文”不能证明该教授没有发表论文。

## 当前更新契约

- 截止日期：`2026-09-01`
- 论文闭区间：`2024-09-01` 至 `2026-09-01`
- 后续运行：每年 3 月 1 日和 9 月 1 日，每次正好覆盖此前两年，首尾均包含
- 分支：`codex/prof-info-YYYY-MM-DD[-NN]`；合并后保留分支
- 集成方式：由 Owner 审核 PR 后进入受保护的 `main`；不自动合并或部署
- 主要作者消歧与论文清单来源：OpenAlex
- 统一采集层：`paperscraper`，覆盖 arXiv、PubMed、bioRxiv、medRxiv、ChemRxiv 与 Semantic Scholar
- 独立核验来源：arXiv；只核验匹配记录，不单独创建论文候选
- 身份/联系基础资料来源：[HKUST(GZ) Faculty Profiles](https://facultyprofiles.hkust-gz.edu.cn/)

## 公开字段

每条教授记录可以公开：

- 官方 Faculty Profiles ID 与 Faculty Profiles 教授页 URL；
- 中英文姓名；
- 公开工作邮箱与公开办公电话；
- 公开个人网站和来源提供的 `permaLink`；
- 当前公开的 GZ 职称、Hub、Thrust 与单位；
- 官方资料公开的 Google Scholar ID（仅作为基础字段保留，不用于论文检索）；
- 根据研究来源推导的研究兴趣、研究领域、关键词和解释性摘要；
- 检索状态、限制、证据链接和最后核验日期；
- 本轮时间窗内检索到、带稳定身份与证据链接的论文。

`officialProfileUrl` 与 `permaLink` 必须分开。Faculty Profiles 界面使用官方 profile ID 打开教授详情；API 的 `permaLink` 可能指向 Institutional Repository 作者页或其他官方学术页面。必须原样保留其来源含义，不能误标为 Faculty Profiles 教授页。

缺失联系方式保持 `null`。禁止推断、合成或复制私人/个人联系方式。

## 仓库结构

```text
All_Prof_Info.md                 # 生成的英文索引
All_Prof_Info.zh-CN.md           # Codex 基于冻结英文索引生成的中文译稿
data/
  professors.json               # 规范化官方人员名单
  research-analysis.json        # 经审核的多来源研究分析
  publications.json             # 经审核和去重的候选
  research-subdirections.json  # 经独立审核的英文子方向与论文归属
  translations.zh-CN.json      # 经审核的精确键中文翻译记忆
  manifest.json                 # schema v2 双语简介/论文允许列表、数量与英文冻结摘要
  directory-index.json          # 轻量公开目录记录
  search-index.json             # 网站使用的有界公开搜索数据
professors/<unit>/
  <slug>.md                      # 生成的英文教授简介
  <slug>.zh-CN.md                # 经审核的中文教授简介
  <slug>.publications.md         # 生成的英文论文详情
  <slug>.publications.zh-CN.md   # 经审核的中文论文详情
reports/
  <cutoff>-roster-diff.json
  <cutoff>-conflicts.json
  <cutoff>-subdirection-self-review.json
scripts/
  sync_professor_information.py
  initialize_research_analysis.py
  prepare_publication_work_queue.py
  retrieve_research_sources.py
  build_publication_candidates.py
  prepare_subdirection_work_queue.py
  validate_subdirection_review.py
  generate_professor_markdown.py
  prepare_chinese_translation.py
  translate_professor_markdown.py
  validate_professor_information.py
tests/
  COVERAGE.md
  test_professor_information.py
```

公开数据分为三级读取：

1. `data/directory-index.json` 提供轻量目录与筛选记录，不包含论文题名或详情。
2. `professors/<unit>/<slug>.md` 及对应 `.zh-CN.md` 提供教授简介和经审核的研究子方向。
3. `professors/<unit>/<slug>.publications.md` 及对应 `.publications.zh-CN.md` 提供论文详情。

`data/manifest.json` 使用 schema version 2。对每个教授 slug，`documents.<slug>.profile.{en,zhCN}` 与 `documents.<slug>.publications.{en,zhCN}` 是仅有的公开 Markdown 路径。`data/search-index.json`、`data/directory-index.json` 和 `data/research-subdirections.json` 仍使用 schema version 1。

[`HKUSTGZ_microelectronics_2025_2026_publications.md`](HKUSTGZ_microelectronics_2025_2026_publications.md) 仅作为历史参考证据保留，不是全校生成数据的事实来源。

## 来源与论文规则

Faculty Profiles 只用于基础字段：当前人员身份、姓名、公开工作联系方式、网站、官方教授页、来源提供的 `permaLink`、Google Scholar ID，以及当前公开的 GZ 任职关系。只有官方资料公开时才保留 Scholar ID；它仅作为基础字段，不用于论文采集。工作流不读取官方教授页中的研究兴趣、研究描述、摘要或论文列表。同步脚本会拒绝空数据、重复 ID、分页总数变化、重复页面，以及只保留不足 95% 基线 ID 的候选名单。每个从基线中消失的 ID 都必须保留在日期化名单差异报告中。

OpenAlex 用于解析教授身份并提供主要论文清单。本仓库只能通过 `paperscraper` 集成 arXiv、PubMed、bioRxiv、medRxiv、ChemRxiv 与 Semantic Scholar。Google Scholar 已明确排除在研究来源计划之外；官方资料中的 Scholar ID 仅作为基础字段保留，不用于论文采集。不得由仓库代码直接请求 Semantic Scholar 接口。`paperscraper` 是客户端库而不是代理服务；其 Semantic Scholar 适配器仍依赖上游服务，因此仍可能遇到访问限制。

独立 arXiv 查询只核验匹配记录，不能自行创建论文候选。研究兴趣、研究领域、关键词、解释性摘要和论文条目都必须与经过 OpenAlex 消歧的教授身份匹配。禁止绕过限流、robots、验证码或访问控制。单个请求在完整首轮失败时先跳过；首轮走完整份名单后仅定点重试失败目标，连续三次定点失败则记为 `failed-skipped`。来源级访问拒绝或限流会立即停止该来源。证据受限时，教授分析只能以 `blocked` 或 `incomplete` 状态并附原因发布。

论文候选必须至少包含经过批准的 OpenAlex、arXiv、PubMed、bioRxiv、medRxiv、ChemRxiv 或 Semantic Scholar URL，并使用闭区间有效日期。跨源去重依次匹配 DOI、arXiv ID、OpenAlex ID、Semantic Scholar ID、PubMed ID，最后使用规范化题名与相容年份。匹配的正式会议/期刊版本优先于预印本，同时保留全部来源 ID 和证据链接。日期冲突或作者歧义必须隔离并交由 Owner 审核。来自 `paperscraper` 或独立 arXiv 核验流程的结果，只有与已消歧的 OpenAlex 记录匹配后才能自动归属。

禁止复制来源摘要。研究摘要与分类必须是原创分析，并由列出的已批准研究证据支持；Faculty Profiles 官方内容不能作为这些推导字段的证据。

## 英文优先的双语工作流

英文是生成流程的主版本。中文文件是译稿，不是独立生成的数据集。

1. 根据教授、研究分析和论文规范数据生成英文子方向队列与候选。
2. 由 Codex 审核英文证据，再运行独立审核命令。审核可以保守发布 `limited`，但不得留下未解决的 `block`。
3. 生成完整英文总览、简介文件、论文文件、目录索引、搜索索引和 manifest。
4. 校验规范化数据、数量、身份、链接、日期窗口、子方向审核和确定性输出；随后按 manifest 摘要冻结英文输出。
5. 由 Codex 审核并填写 `data/translations.zh-CN.json`，再通过遇到缺项即失败的翻译命令生成对应的中文总览、简介和论文文件。
6. 中文论文条目必须保留完整英文原题，并增加中文译名；不得用译名替换书目题名。
   发表场所名称和逐篇论文的来源关键词保留其发表语言，避免改写书目专名和精确检索词。
7. 运行两个发布校验器和完整单元测试套件。每份英文文档都必须有中文对应文件，且教授/论文身份、外部链接、数量、顺序、标题层级和标题结构一致。

翻译阶段不得修改 `professors.json`、`research-analysis.json`、`publications.json`、搜索索引或英文 Markdown。
翻译记忆以精确英文展示文本为键。缺失或空白译文会在写入任何中文文档前中止，因此不完整翻译记忆不会产生半翻译目录。

子方向审核状态采用保守语义。`pass` 表示该教授的所有已接受子方向均通过审核；任一子方向存在明确证据限制时，该方向及教授聚合状态都必须为 `limited`；`block` 表示问题未解决，不能发布。仅由官方教授页支持的方向必须是 `limited`，且限制说明必须重复为第三句解释。论文只有在提供明确英文原因时才可不归入任何子方向。

## 前置条件与制品策略

- 从干净 clone 运行，并确保 Git、Python 3 与 pip 可用。
- 实时检索前通过 `requirements.txt` 安装固定版本 Python 依赖；单元测试与校验器不需要服务凭据。
- 可选服务凭据只能通过受支持的环境变量提供，绝不能存入仓库。
- 截止日期必须为 3 月 1 日或 9 月 1 日；闭区间起始日期正好是两年前的同一日。

规范输入、经审核输出、公开索引、双语 Markdown、`data/manifest.json`、日期化名单/冲突报告，以及 `reports/<cutoff>-subdirection-self-review.json` 都是跟踪的发布制品。可恢复检索证据、论文/翻译/子方向工作队列、未经审核的子方向候选与提示词、翻译分片、子方向分片、`.sync-cache/`、`.venv/`、Python 字节码，以及内部 agent 日志/历史/评估制品均属于工作材料，必须保持忽略或置于仓库外。不得把被忽略的队列、提示词、分片或内部评估制品提升为发布文档。

## 更新命令

使用干净 clone 和日期分支：

```bash
git switch main
git pull --ff-only
git switch -c codex/prof-info-2026-09-02
```

同步官方名单：

```bash
python3 scripts/sync_professor_information.py \
  --cutoff 2026-09-01
```

创建确定性的 Codex 检索队列：

```bash
python3 scripts/initialize_research_analysis.py \
  --cutoff 2026-09-01

python3 scripts/prepare_publication_work_queue.py \
  --start 2024-09-01 \
  --cutoff 2026-09-01
```

初始化脚本拒绝覆盖已有分析文件，因此恢复运行时不会静默清除已经完成的工作。

安装固定版本的采集依赖后，由 Codex 处理检索队列。OpenAlex API key 只从受保护的 `OPENALEX_API_KEY` 环境变量读取；如有 Semantic Scholar 凭据，只能通过 `paperscraper` 支持的环境变量提供。测试无需凭据，任何凭据都不得写入仓库。检索先写入可恢复证据；候选构建再把 `paperscraper` 结果与 OpenAlex 匹配，执行独立 arXiv 核验，并对全部已批准来源去重：

```bash
python3 -m pip install -r requirements.txt

python3 scripts/retrieve_research_sources.py \
  --start 2024-09-01 \
  --cutoff 2026-09-01

python3 scripts/build_publication_candidates.py \
  --cutoff 2026-09-01
```

检索器使用被 Git 忽略的原始断点、明确的请求超时，并且标准输出只显示进度。请求在完整首轮失败时先跳过，随后最多定点重试三次。来源级访问拒绝或限流会阻止该来源继续请求。Codex 必须先审核证据与冲突报告，再写入研究分析或生成文档。

可用 `--sources` 只恢复指定的已批准来源。如果三次有记录的限时探针已经证明某个来源在本轮整体不可用，则通过 `--blocked-sources` 记录；这样可以为每位教授保留限制状态，而不会重复数百次已知失败请求。适配器仍保留，并会在之后的定期更新中再次尝试。

生成公开文档前，先准备并独立审核英文子方向：

```bash
python3 scripts/prepare_subdirection_work_queue.py \
  --root . \
  --cutoff 2026-09-01

python3 scripts/validate_subdirection_review.py \
  --root . \
  --cutoff 2026-09-01 \
  --write-reviewed

python3 scripts/validate_subdirection_review.py \
  --root . \
  --cutoff 2026-09-01
```

准备命令会在 `reports/` 下写入被忽略的工作队列与未经审核候选；本次发布应输出 `397/397`。`--write-reviewed` 会拒绝过期的准备输入、重新计算独立审核、拒绝任何 `block`，并写入被跟踪的 `data/research-subdirections.json` 与日期化自审报告。仅校验命令会从规范数据重新计算队列和审核，并要求结果与两个跟踪制品完全相等。

```bash
python3 scripts/generate_professor_markdown.py \
  --start 2024-09-01 \
  --cutoff 2026-09-01 \
  --generated-at 2026-09-02T00:00:00+08:00

python3 scripts/prepare_chinese_translation.py \
  --start 2024-09-01 \
  --cutoff 2026-09-01

python3 scripts/translate_professor_markdown.py \
  --start 2024-09-01 \
  --cutoff 2026-09-01
```

准备命令只把缺失的精确键字符串写入日期化翻译输入报告。Codex 审核并合并这些译文到翻译记忆后，再生成中文 Markdown 并校验：

```bash
python3 scripts/validate_subdirection_review.py --root . --cutoff 2026-09-01
python3 scripts/validate_professor_information.py --root . --cutoff 2026-09-01
python3 -m unittest discover -s tests -p 'test_*.py'
```

对当前发布，两个校验器都必须输出 `397/397`，且完整发现的测试套件必须通过。任何非零退出、数量不符、schema/截止日期/窗口漂移、不安全或缺失的 manifest 路径、过期审核制品、`block` 状态、不可发布的检索状态、冲突、冻结英文摘要不符、翻译缺失，或双语结构/身份/链接/顺序不一致，都会中止发布。

使用相同参数再次运行生成器，英文生成输出必须零差异。任何 commit 或 push 之前，都要审核精确候选差异、未解决限制、生成数量和双语一致性，并执行仓库的 clean-before-commit 门槛。

## 规范数据结构

`data/research-analysis.json` 是与官方基础资料分离的 JSON 数组。经审核记录的最小结构如下：

```json
{
  "officialProfileId": "20",
  "researchInterests": ["Data management"],
  "researchAreas": ["Database systems"],
  "keywords": ["databases", "knowledge graphs"],
  "summaryEn": "基于证据原创撰写的英文解释。",
  "status": "complete",
  "notes": [],
  "evidenceUrls": ["https://openalex.org/A...", "https://arxiv.org/abs/..."],
  "lastVerifiedOn": "2026-09-01"
}
```

该文件中的每个证据 URL 都必须来自已批准的研究来源域名。`complete` 记录必须有证据；`incomplete` 或 `blocked` 记录必须说明限制。

`data/publications.json` 是 JSON 数组。经审核记录的最小结构如下：

```json
{
  "officialProfileId": "20",
  "title": "完整英文原题",
  "effectiveDate": "2025-08-01",
  "publicationType": "conference",
  "venue": "Example Conference",
  "doi": "10.x/example-or-null",
  "arxivId": "2501.00001-or-null",
  "keywords": ["keyword"],
  "evidenceUrls": [
    "https://openalex.org/W...",
    "https://arxiv.org/abs/2501.00001"
  ]
}
```

禁止用占位项增加数量。只有当每位教授都通过日期化证据说明明确标记为 `blocked`、`incomplete` 或 `complete` 时，空数组才有效；`not-started` 不能发布。

`data/research-subdirections.json` 是 schema version 1 对象，内部按教授记录组织。每位教授包含聚合 `reviewStatus`、至少一个英文 `subdirections` 项，以及其每篇论文的一条归属记录。每个子方向包含稳定 ID、英文名称、恰好三句互不重复的解释、获准的证据 URL、证据论文 ID，以及 `pass` 或 `limited` 状态；受限方向还必须包含 `limitationEn`。每条论文归属要么列出已接受的子方向 ID，要么给出明确的 `unassignedReasonEn`。

## 网站契约

网站只读取固定公开仓库的 `main` 分支。它要求 manifest schema version 2，第一级读取 `data/directory-index.json`，随后两级只读取 manifest 允许列表中的英文或中文简介与论文路径。所有 Markdown 均视为不可信输入：禁止执行原始 HTML、脚本、iframe、图片和危险 URL；拒绝跳转、无效 UTF-8、超大内容和任意路径；重新验证失败后不提供超过新鲜期的缓存内容。

## 审核与发布

每年 3 月 1 日和 9 月 1 日运行此工作流：截止日期就是窗口终点，闭区间起点为两年前的同一日。两个校验器和完整测试套件通过后，只审核并暂存预期的跟踪制品。只有在另行授权时，Codex 任务才能准备日期分支、commit、push 和 PR。Owner 负责审核和合并。修改网站前，必须回读 GitHub `main`，确认 manifest schema version 2 与 `data/directory-index.json` 已就绪。打 tag 与网站部署是合并后的独立动作，必须另行授权。
