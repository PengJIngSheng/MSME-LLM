# 性能与并发优化报告

分支: `perf/gemma4-e4b-concurrency-scrapling`  ·  日期: 2026-08-06

## 1. 卡顿根因（实测，非推断）

### 根因 A：每请求变动 num_ctx，触发整模型重载

| 操作 | 耗时 |
|---|---|
| 相同 num_ctx 重复请求 | 0.61 s |
| **改变 num_ctx** | **7.8 s（每次）** |
| 冷启动 | 29.4 s |

Ollama 在加载模型时分配 KV cache。传入不同 `num_ctx` 会导致卸载并按新窗口重载，
**重载期间阻塞所有其他在途请求**。原代码有三处各自决定 num_ctx：

- `server.py` 主聊天：按模式派生 1024 / 2048 / 6144 / profile 值
- `server.py:423` 查询改写：硬编码 2048
- `server.py:517` 图片路由：硬编码 2048（且 keep_alive 仅 8m）

后两处与主聊天**跑在同一个模型**上，所以每次分类都在 2048↔16384 之间来回重载。

### 根因 B：OLLAMA_NUM_PARALLEL 未设置，请求串行化

| 并发数 | TTFT 中位数 | 聚合吞吐 |
|---|---|---|
| 1 | 0.55 s | 77 tok/s |
| 10 | **18.35 s** | 70 tok/s |

### 根因 C：磁盘 100% 写满（72G 用满，剩余 3.7M）

## 2. 修复与实测效果

### 模型对比（相同参数：num_ctx=4096, num_predict=192）

| 场景 | gemma4:12b | gemma4:e4b | 提升 |
|---|---|---|---|
| 10 并发 · 总耗时 | 27.4 s | 11.2 s | 2.4× |
| 10 并发 · TTFT 中位 | 18.35 s | 5.53 s | 3.3× |
| 10 并发 · 聚合吞吐 | 70 tok/s | 167 tok/s | 2.4× |
| 5 并发 · 失败数 | 2 | 0 | — |

**E4B 更快是实测结论。** 注意它是 8.0B 参数但文件 9.6GB（比 12b 的 7.6GB 还大，
因为 E 系列的 per-layer embedding 结构），所以"参数少所以快"的直觉并不成立，必须实测。

### 累计效果（基线 → 最终配置）

| 并发 | 基线 wall | 最终 wall | 基线 TTFT | 最终 TTFT | 基线吞吐 | 最终吞吐 |
|---|---|---|---|---|---|---|
| 1 | 2.5 s | 1.6 s | 0.55 s | 0.58 s | 77 | 118 |
| 5 | 8.2 s (2 失败) | 3.7 s | 3.03 s | **0.68 s** | 71 | 252 |
| 10 | 27.4 s | 7.1 s | 18.35 s | **2.82 s** | 70 | 266 |
| 20 | 34.2 s | 11.4 s | 16.90 s | **5.15 s** | 112 | 325 |

10 并发下：**总耗时 3.9×、TTFT 6.5×、吞吐 3.8×**，且全并发级别零失败。

## 3. 变更清单

| 项 | 变更 |
|---|---|
| Ollama 服务 | 新增 `/etc/systemd/system/ollama.service.d/10-concurrency.conf`：NUM_PARALLEL=4, MAX_LOADED_MODELS=2, MAX_QUEUE=256, KEEP_ALIVE=30m |
| 模型 | gemma4:12b → gemma4:e4b（12b 保留作回滚） |
| num_ctx | 三处调用点统一为 `cfg.ollama_num_ctx`（16384） |
| keep_alive | 8m/45m 统一为 45m |
| 中文 | UI 选项、locale、翻译表、prompt 全部移除；旧 `pepperLang=zh` 自动迁移至 en |
| 安全 | 新增 `Model Networking/url_guard.py`（SSRF 防护），接入 fetch 路径 |
| Web Search | 输出增加来源/时间/提取状态/截断标记/错误状态 |
| 测试 | 154 → 197（新增 43） |

## 4. Scrapling 评估结论：不采用

POC 实测 15 项，12 项通过。**不采用的决定性原因：**

| 能力 | 结果 |
|---|---|
| 搜索引擎 / SERP | ❌ **完全没有** —— 只能抓取和解析，无法替代 Brave/Tavily |
| 自动正文提取 | ❌ 无内置 boilerplate 过滤，需按站点手写 CSS 选择器 |
| 静态抓取速度 | ✅ 0.05 s（很快） |
| 异步并发 | ✅ 3/3 成功 |
| 错误处理 | ✅ 404 返回状态码，DNS/超时抛可捕获异常 |
| 依赖冲突 | ⚠️ 强升 lxml 5.3→6.1.1，与 crawl4ai 的 pin 冲突 |

**核心理由**：本项目的搜索层需要 SERP，Scrapling 没有；抓取层需要对**任意域名**自动提
取正文，crawl4ai 的 PruningContentFilter 能做，Scrapling 需要每站点手写选择器（实测在
Wikipedia 上手写选择器仅减少 9% 噪声：50216 → 45885 字符），对搜索场景不可维护。

已卸载 Scrapling 并将 lxml 恢复至 5.4.0，`pip check` 无冲突。

## 5. 回滚方案

```bash
# 回滚模型（gemma4:12b 未删除，仍在本地）
# 编辑 config/server.ubuntu.yaml：think_model/fast_model 改回 gemma4:12b
sudo systemctl restart bisnes-ai

# 回滚 Ollama 并发配置
sudo rm /etc/systemd/system/ollama.service.d/10-concurrency.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama

# 回滚全部代码改动
git checkout main
```

详见 `docs/MODEL_ROLLBACK.md`。

## 6. 多用户容量（生产参数实测）

前面第 2 节用的是轻量参数（短提示、num_predict=192）。下表改用**生产参数**
（长提示、num_predict=1024），是真实容量的依据。

### NUM_PARALLEL 调优：中位 TTFT

| 槽位 | 5 用户 | 10 用户 | 20 用户 | 30 用户 | 显存 |
|---|---|---|---|---|---|
| 4 | 0.71 s | 11.79 s | 23.76 s | — | ~14 GB |
| 8 | 0.67 s | 0.84 s | 20.19 s | — | ~10 GB |
| **16（采用）** | — | **0.83 s** | **1.39 s** | **1.72 s** | ~13 GB |

### 最终容量画像（NUM_PARALLEL=16）

| 并发 | TTFT 中位 | TTFT p95 | 完成中位 | 聚合吞吐 | 失败 |
|---|---|---|---|---|---|
| 1 | 0.52 s | 0.52 s | 6.1 s | 168 tok/s | 0 |
| 10 | 0.83 s | 0.89 s | 22.7 s | 452 tok/s | 0 |
| 20 | 1.39 s | 34.3 s | 33.8 s | 454 tok/s | 0 |
| 30 | 1.72 s | 34.6 s | 34.1 s | 478 tok/s | 0 |

**结论**：约 15 人同时提问可流畅服务（首字 <1s）；20–30 人时首字仍在 2 秒内，
但完整回答时间拉长到 35 秒左右，因为算力被均摊。瓶颈已从"排队等模型重载"
变成"GPU 算力均分"——这是正常且可预期的饱和行为，不再是卡死。

### GPU 落位（已确认）

```
load_tensors: offloaded 43/43 layers to GPU
srv load_model: n_slots = 16, n_ctx_slot = 16384
ollama ps:  gemma4:e4b   100% GPU   CONTEXT 16384
nvidia-smi: llama-server  8620 MiB (GPU0) + 4122 MiB (GPU1)
```

43/43 层全部在 GPU，无 CPU offload。两张 RTX 5090 共用 12.7 GB / 64 GB。

### 第四处 num_ctx 不一致（补修）

`ImageGemma4/image_gemma4.py` 用 `min(num_ctx_cap, 8192)`=8192，同样跑在
`fast_model` 上，已改为 `cfg.ollama_num_ctx`。另外新增
`OLLAMA_CONTEXT_LENGTH=16384` 作为服务层兜底——不带 `num_ctx` 的请求原本会
按模型训练上限 131072 加载，与应用的 16384 互相重载。

## 7. pgvector 排查（对话缓存与知识库）

### 发现的四个问题

**① 93,585 个向量上没有任何向量索引**

`langchain_pg_embedding` 894MB / 93,585 行，`embedding` 列上既无 HNSW 也无
IVFFlat。每次检索都是全表顺序扫描：单次 **306 ms**、读取 **~765,000 个缓冲页
（≈6 GB）**、占用 3 个后端进程（含 2 个并行 worker）。

根因：LangChain 建表时把列建成了**无维度的 `vector`**（项目自建的
`web_content_cache` 反而是正确的 `vector(768)`），而 HNSW 要求固定维度。

**② `collection_id` 上也没有索引**

对话记忆只有 221 行，却和 93,364 行知识库同表。取记忆要扫完全部 93,585 行。

**③ SQLAlchemy MetaData 竞态 —— 每次重启后首个对话必然丢失知识库**

`memory_agent` 与 `knowledge_agent` 各自构造 `PGVector`，而 langchain_postgres
把 ORM 表注册在共享的 declarative base 上，该注册非线程安全。server.py 用
`asyncio.gather` 并发初始化两者：

```
[Knowledge RAG] Retrieval Error: Table 'langchain_pg_collection' is already
                defined for this MetaData instance.
```

两个检索双双返回空，且因为调用点都有优雅降级，**失败是静默的**。生产日志确认
用户测试时段（15:22）正在发生。这就是"问 SSM/LHDN 期限却只得到泛泛合规建议"的
直接原因——知识库里有 LHDN 内容，但从未送到模型。

**④ web_content_cache 从不清理过期数据**

1064 行全部已过期，代码中没有任何 purge 逻辑，会无限累积。

### 修复与实测

| 项 | 修复 | 效果 |
|---|---|---|
| ① | `ALTER COLUMN embedding TYPE vector(768)`（锁表 5.3s）+ HNSW 索引（365MB，36.5s 构建） | 知识库检索 **536ms → 4.9ms** |
| ② | `CREATE INDEX CONCURRENTLY lpe_collection_id_idx` | 对话记忆 **306ms → 5.2ms** |
| ③ | 新增 `pgvector_store.py`，进程级锁序列化构造 | 5/5 全新进程首轮均成功（修复前失败） |
| ④ | 手动清理 1064 行过期数据 + VACUUM | — |
| 附带 | 表重写顺带消除膨胀 | 主键索引 34MB → 5.4MB |

PostgreSQL 参数同步调整：`shared_buffers` 128MB → **4GB**（库仅 903MB，现可全量
缓存）、`max_connections` 100 → 200、`work_mem` 4MB → 32MB、
`effective_cache_size` → 12GB。

### 检索层并发容量（3 秒预算内）

| 并发 | 总耗时 | 中位 | p95 | 超预算 | 知识为空 | 报错 |
|---|---|---|---|---|---|---|
| 10 | 0.26 s | 0.22 s | 0.26 s | 0 | 0 | 0 |
| 30 | 0.64 s | 0.41 s | 0.63 s | 0 | 0 | 0 |
| 50 | 1.06 s | 0.68 s | 1.05 s | 0 | 0 | 0 |

PG 连接峰值 6/200。

### 启动预热（已实施）

`server.py` 的启动钩子现在会构造两个 store 并各开 5 个池化连接
（`pgvector_store.prewarm`），数据库侧可见 10 个 idle 连接在启动后 0.13 秒内建立。

实测首波 5 并发：

| | 首波耗时 | 超 3s 预算 | 知识为空 |
|---|---|---|---|
| 未预热 | 0.21 / 0.24 / 0.24 s | 0/5 | 0/5 |
| 预热后 | 0.16 / 0.16 / 0.15 s | 0/5 | 0/5 |

**一处归因更正**：先前记录的"首波 3.22 s、4 个请求超预算"曾被归因于连接池冷
启动。该数字无法在预热关闭时重现——真实原因是缓冲区冷启动（当时刚建完 365MB
的 HNSW 索引，首次查询需从磁盘读入索引与表页），已由 `shared_buffers`
128MB → 4GB 解决。

预热保留的价值在别处：稳定省下 70-80 ms，且把两个 store 的构造挪到启动时**顺序
执行**，使第 ③ 项的 MetaData 竞态不再依赖锁去赢，而是根本不发生。

## 8. 知识库构成审计

### 近一半是马来语词典，且重复灌了三遍

| 来源 | chunks | 占比 |
|---|---|---|
| `Finetune/dictionary/kamus_dewan_cleaned.jsonl` | 26,706 | 28.6% |
| `Finetune/dictionary/Kamus-dewan-bahasa-edisi-keempat.pdf` | 11,930 | 12.8% |
| `Finetune/dictionary/kamus_dewan_cleaned.json` | 6,941 | 7.4% |
| **词典小计** | **45,577** | **48.8%** |
| `Finetune/knowledge/knowledge_base.cleaned.jsonl` | 23,960 | 25.7% |
| `Finetune/knowledge/final_ai_training_data.cleaned.jsonl` | 23,721 | 25.4% |
| `Finetune/msme/msmelatest.json` | 62 | 0.1% |
| `Finetune/msme/bank.json` | 44 | 0.0% |

同一部《Kamus Dewan》以 jsonl、json、PDF 三种形式各灌了一遍。而 MSME 专有资料
（`msme/`）只有 106 个 chunk，占 0.1%。

### 干扰程度：不均匀，但对短问题是灾难性的

前 10 检索结果中词典条目的占比：

| 查询 | 词典占比 |
|---|---|
| LHDN e-invoice deadline for small business | 0/10 |
| SSM company registration requirements | 0/10 |
| SST registration threshold Malaysia | 0/10 |
| grants and financing for MSME | 0/10 |
| how to improve cash flow for a retail shop | 0/10 |
| **what is SST** | **10/10** |

长而具体的问题不受影响，但 `"what is SST"` 这类**短定义式问题会被词典完全劫持**
（命中 `stet`、`ssm i`、`ssb`、`sakhlat` 等条目），而这正是 MSME 用户高频提问的形式。

### 0.37 阈值恰好构成有效防线

| 查询 | 最佳距离 | 结果 |
|---|---|---|
| `what is SST` → 词典条目 | 0.401 – 0.439 | 全部 > 0.37，**注入 0 字符** |
| `what is LHDN e-invoice` → 业务内容 | 0.231 – 0.256 | 全部 < 0.37，注入 5230 字符 |

阈值挡住了词典劫持。但这是靠距离侥幸分开的，不是结构性隔离——词典仍占据
48.8% 的索引体积和 HNSW 构建成本，且任何一次阈值调整都可能让它重新涌入。

**建议**：把词典移出 `mof_finetune_knowledge` 集合。若语言功能确实需要它，应放进
独立集合按需查询，而不是混在业务问答的检索路径里。此操作会删除 45,577 个 chunk，
属破坏性变更，未执行。

## 9. MongoDB 索引

`chats` 集合（392 条 / 0.7 MB）此前只有 `_id` 索引，`/api/history` 的
`{user_id}` 过滤 + `updated_at` 排序走全集合扫描。

已补 `chats_user_updated_idx {user_id: 1, updated_at: -1}`，扫描文档数
392 → 39（仅读需要的），排序阶段消除。

**但耗时前后都是 0 ms** —— 集合太小、完全驻留内存，当前并非瓶颈。这与 pgvector
的情况有本质区别（那里 93,585 个向量 / 894 MB，缺索引实测 306 ms）。此索引是
防止随用量增长后劣化的预防措施，不是对既有性能问题的修复。
