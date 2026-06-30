# rare-disease-epi · 罕见病流行病学情报

一个**可移植的** Agent Skill（可在 Claude Code / Qwen Code / Kimi Code CLI / MiniMax Mini-Agent 直接用）。
你说一句「**查一下 X 病的患病率**」，它就把这个罕见病的流行病学数据（患病率 / 发病率 / 死亡率 / 携带频率）
整理成报告，覆盖 **国际（Orphanet/PubMed）+ 美国 + 中国**，并优先查**国内外诊疗指南/专家共识**当口径锚点。

它和普通问答最大的不同：**每个 epi 数字都带来源徽标 + 采集年份 + 样本量**；同病同国多个数字**排成对比表解读冲突、绝不取平均**；
遗传病**分亚型给数、携带频率与临床患病率分开**；结尾附一张 **「🔴 需人工核对清单」**。
目的不是给一个患病率数字，而是**给一个你能放心引用的口径**。

> 仅供研究/情报参考，**非医疗建议、非诊断**。

> 🔰 **第一次用、或不太懂技术？** 先看 [`GETTING_STARTED.md`](GETTING_STARTED.md)（保姆级：开联网、放行网站、四种 CLI 安装、看懂徽标、出错一键修）。

本工具直接复用了 `drug-intel` 的架构骨架：**七档来源徽标、暂停门(Pause Gates)、末尾「🔴 需人工核对清单」、
确定性脚本优先、文字/可视化双输出**；只把"数据源 + 工作流"换成罕见病 epi，并新增一档 **📘指南/共识** 徽标。

---

## 它能帮你查什么

说出病名后，你会拿到这些内容（每条都带徽标）：

- **疾病身份** —— ORPHAcode / OMIM / ICD-10·11 / GARD / 别名 / 遗传方式，先把病对准、不认错
- **共识锚点（📘）** —— 国内外诊疗指南 / 专家共识 / GeneReviews，给出**病例定义、诊断标准、亚型分类**
- **亚型矩阵** —— 按指南分型分别给 epi；**携带频率 vs 临床患病率分列**
- **各国 epi 对比表（10 列）** —— 年份 / 样本量 / 患者类型 / metric / 数值±CI / 地理 / 分母 / 病例定义 / 设计 / 来源；表后**冲突解读**
- **中国侧（副驾）** —— 工具给检索式、你贴原文、它归一化进对比表，每条 🔴
- **🔴 需人工核对清单** —— 中国侧每项、每个死亡率、每个外推值都进清单

---

## 最高原则

与 drug-intel 一致——**宁可 `❓N/A` 也不编**。每个 epi 数字都必须带徽标、年份、样本量。
本工具的价值不是"给一个患病率数字"，而是**把每个数字的口径与可信度摊开，把不确定的交到人手上核对**。

---

## 徽标怎么读（七档）

| 徽标 | 意思 |
|------|------|
| 🏛️ 官方 | **最高可信**：各国官方疾病登记/流调的**登记数**（如日本受給者証、韩国 KDCA、官方流调）。仅对客观登记数 |
| ✅ 库 | 权威数据库可核实（Orphanet / PubMed / ClinicalTrials.gov）。与 🏛️官方 同档 |
| 📘 指南 | 诊疗指南 / 专家共识 / GeneReviews。高等级，但其 epi 数字为二手值 → **须溯原始研究**填年份/样本/患者类型 |
| 🔍 web | 单篇研究 / 联网检索（附 PMID/URL）|
| ⚠️ | 单中心小样本 / 弱源，建议再确认 |
| 🔴 | 高幻觉 / 必须人工核——**绝不会没来源就给数字** |
| ❓ N/A | 认真查过、确实没找到（写明查了哪些源）|

可信度：**🏛️官方 ＝ ✅库 ＝ 📘指南 ＞ 🔍web ＞ ⚠️ ＞ ❓N/A**。🔴 是独立的"必核"标，可叠加在任何来源上。

> **强制 🔴**（不因来源权威而解除）：**整个中国侧**、所有**死亡率**、所有**外推/建模患病率**、跨研究**代表值取舍**、**指南未溯源**的数字。

---

## 两半不对称（本 skill 的核心设计）

- **可自动化半边 → 脚本当"抓取器"**：Orphanet、PubMed E-utilities（MeSH 限定）、ClinicalTrials.gov、GARD。写进 `scripts/fetch_epi_data.py`。
- **不可自动化半边（中国）→ 脚本当"副驾"**：知网/万方/维普/微信公众号/患者组织白皮书/学会共识**无开放 API**，脚本**不抓**，改为生成检索式 + 接收用户粘贴 + 归一化进对比 schema。中国侧默认 🔴。

> 让脚本去抓知网/公众号必然失败并误导用户。中国侧 = query 优化 + 结构化对比 + 标红，不是检索。

---

## 单独跑数据脚本（可选）

```bash
python scripts/fetch_epi_data.py "Spinal muscular atrophy" --orphacode 83330
python scripts/fetch_epi_data.py "Gaucher disease" --max-studies 150 --compact
```

| 参数 | 说明 |
|------|------|
| 病名（必填） | 中/英文病名；含空格加引号。PubMed 会自动映射到 MeSH 词 |
| `--orphacode N` | Orphanet 编号，用于精准取患病率/亚型（**强烈建议**）|
| `--gard-id N` | GARD 编号（否则从 Orphanet 自动取）|
| `--max-studies N` | ClinicalTrials.gov 最多抓多少条（默认 200）|
| `--no-studies-list` | 只保留汇总、去掉逐条试验明细 |
| `--compact` | 单行 JSON |

**脚本能可靠拿到的**（可直接打 ✅）：Orphanet 患病率带/亚型/指南链接（域名放行时）、PubMed 各 metric 命中与种子 PMID、
美/中 affiliation 命中、指南 + GeneReviews 命中、CT.gov 入组 proxy。

**脚本拿不到、走 web + 人工的**（返回 `null` + 检索式/URL）：中国侧全部（知网/公众号/白皮书/学会共识）、GBD-IHME 旁证、
以及 Orphanet 被域名挡掉时（→ 用 `web_fetch` 抓 `www.orpha.net/en/disease/detail/<编号>` 回退）。

> 关键约定：**某个源 `null` ≠ 病不存在 / 无 epi**。Orphanet 被 403、超罕见病在某库查不到都属正常环境问题。

---

## 生成可视化报告（可选）

```bash
python scripts/build_report.py <data.json> -o report.html         # 自包含 HTML，可"打印→存为 PDF"
python scripts/build_report_docx.py <data.json> -o report.docx    # 可编辑 Word（需 python-docx）
```

读 `templates/report_schema.json` 格式的 JSON，输出**完整文档**（身份卡 + 共识锚点 + 亚型矩阵 + 10 列 epi 对比表 +
估计值随年份时间线 + 🔴 核对清单 + 参考来源）。三格式（HTML/PDF/Word）口径一致。

> 正常用你**不用碰这些脚本**——直接对 Claude 说「出一份 HTML/PDF 报告」即可，它会自动填好 JSON 再调用。

---

## v1 / v2 范围

- **v1（本版，已完整跑通）**：中国副驾 + PubMed + Orphanet + 指南/共识 主线；schema、亚型、徽标、暂停门、脚本、双输出、核对清单全部到位。
- **v2（已留清晰 TODO stub，见 SKILL.md §11）**：日本「指定難病」受給者証所持者数年度登记、韩国 KDCA 희귀질환 산정특례统计的抓取与解析（日/韩文页面需 parser）。接口位置已在 `fetch_epi_data.py` 与 SKILL.md 标好。

---

## 文件结构

```
rare-disease-epi/
├── SKILL.md                      # 运行时流程：七档徽标 + 暂停门(G0–G3) + 指南用法 + 两半不对称 + 10列schema + 亚型 + 双输出 + v2 stub
├── GETTING_STARTED.md            # 新手保姆级（联网 + 域名白名单含 Orphanet + 四 harness 安装 + sample run）  ← 第一次用看这个
├── scripts/
│   ├── fetch_epi_data.py         # 确定性采集器（纯标准库，无需 pip，永远第一步）
│   ├── build_report.py           # 可视化报告生成器（JSON → 自包含 HTML，可打印成 PDF）
│   └── build_report_docx.py      # Word 版生成器（同一份 JSON → 可编辑 .docx，需 python-docx）
├── templates/
│   ├── report_schema.json        # 可视化报告的数据契约（build_report 的输入格式）
│   └── report_template.html      # 七档徽标配色 / 章节骨架的设计参考
├── examples/
│   ├── sma-epi-data.json         # SMA 可视化报告的输入数据（已带徽标）
│   ├── sma-epi-report.html       # 可视化报告样例（亚型矩阵 + 10列对比表 + 时间线 + 红色清单）
│   ├── sma-epi-report.docx       # 同一报告的 Word 版
│   └── sma-text-report-sample.md # 文字版样例报告（端到端 demo）
└── README.md                     # 本文件
```

## 依赖

Python 3.8+（`fetch_epi_data.py` 与 `build_report.py` 只用标准库）；能联网访问 eutils.ncbi.nlm.nih.gov /
www.clinicaltrials.gov / www.orpha.net / www.orphadata.com。Word 版 `build_report_docx.py` 需 `pip install python-docx`。
转 PDF：浏览器对 HTML "打印→存为 PDF"，或 `weasyprint`，或 `pdf` skill。
