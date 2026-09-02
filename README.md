# Yelp Review AI Agent — Core Service Skeleton

面向西雅图本地 B2B 商户（餐厅 / 房产中介等）的 Yelp 评论自动分析 + 自动回复草稿 + 通知 Agent。

## 为什么这样选型

| 组件 | 选型 | 面试中要讲的点 |
|---|---|---|
| Web 框架 | FastAPI (async) | 原生 async/await，天然适合 I/O bound 的 LLM 调用与并发通知 |
| Agent 编排 | LangGraph 风格的显式状态机（本骨架用轻量自研版本演示原理，生产建议直接上 LangGraph） | 展示你理解"图状态机"而不是把 Agent 写成一坨 if-else |
| 缓存 / 限流 | Redis + Lua 脚本（令牌桶） | 原子性、无锁、单实例吞吐可到几万 QPS，能讲清楚为什么不用应用层内存计数 |
| 语义缓存 | Embedding + 余弦相似度去重 | 避免"重复/近似评论"重复调用大模型，直接省 token 成本 |
| 流式响应 | SSE (Server-Sent Events) | 前端可以边生成边展示草稿，比 WebSocket 更轻量，单向场景足够 |
| 通知 | 异步任务（arq worker，基于 Redis Queue） | 展示你知道"LLM 调用"和"发送通知"要解耦，不能塞在同一个请求里 |
| 向量检索 | Qdrant（也可换 pgvector） | 商户历史评论 / 品牌语气知识库做 RAG，让回复贴近该商户的语气 |
| 多租户隔离 | 每个商户一个 tenant_id，贯穿限流 key / 缓存 key / 向量 collection | 面试官最爱问的"如何做多租户隔离"提前给答案 |

## 目录结构

```
app/
  main.py                 # FastAPI 入口，生命周期管理（Redis/HTTP client 连接池）
  core/
    config.py             # 环境变量集中管理
    rate_limiter.py        # Redis 令牌桶限流（Lua 原子操作）
    cache.py               # 语义缓存（embedding 相似度）
    llm_client.py           # LLM 调用封装：流式、重试、多供应商 fallback
  agents/
    review_agent.py         # 显式状态机：抓取->分类->生成->人工确认->通知
  models/
    schemas.py              # Pydantic 数据模型
  services/
    notifier.py             # 邮件/短信通知（异步、幂等）
  api/
    routes.py               # HTTP/SSE 路由
```

## 快速开始（本地）

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

需要本地起一个 Redis：`docker run -p 6379:6379 redis:7`

## 已知的设计取舍（面试时主动讲出来，见聊天正文第 5 部分）

1. Yelp Fusion API 官方不提供评论全文批量抓取（ToS 限制），生产环境需要走 Yelp Partner / 第三方评论聚合 API，或先做「商户手动同步」兜底。
2. 所有"自动回复"默认走人工审核 Gate，不做全自动发布——这是产品/安全判断，不是技术限制。
3. Agent 状态必须持久化到数据库（而不是进程内内存），否则一次 worker 重启就会丢失所有进行中的工作流。
