# 模型回滚信息

记录时间: 2026-08-06T12:54:30+08:00
分支: perf/gemma4-e4b-concurrency-scrapling

## 变更前已安装的模型
```
NAME                       ID              SIZE      MODIFIED
gemma3:12b                 f4031aab637d    8.1 GB    5 days ago
gemma4:12b                 4eb23ef187e2    7.6 GB    6 days ago
gemma4:26b                 5571076f3d70    17 GB     3 weeks ago
nomic-embed-text:latest    0a109f422b47    274 MB    3 months ago
```

## 恢复命令
```bash
# 恢复被删除的模型（会重新从 registry 下载）
ollama pull gemma3:12b     # 8.1 GB — 项目未引用，仅为释放空间而删
ollama pull gemma4:26b     # 17 GB  — local.windows profile 的旧默认模型
```

## 回滚模型（未删除，始终保留）
```
  Model
    architecture        gemma4
    parameters          11.9B
    context length      262144
    embedding length    3840
    quantization        Q4_K_M
    requires            0.30.5

```
