# 运行与安全

## 环境变量

| 变量 | 说明 |
|---|---|
| `BLACKBOARD_DB_PATH` | SQLite 数据库路径，默认 `data/blackboard.db`。 |
| `BLACKBOARD_API_KEY` | 设置后，所有写操作都必须携带 `X-API-Key`。 |
| `CORS_ORIGINS` | 允许的来源，使用逗号分隔；生产环境不要保留默认 `*`。 |
| `HEARTBEAT_TIMEOUT` | 智能体离线超时秒数。 |
| `WEBSOCKET_MAX_MESSAGE_BYTES` | WebSocket 入站消息上限。 |
| `WEBSOCKET_MAX_CHANNELS` | 单连接订阅频道上限。 |

## 生产建议

1. 设置强随机 `BLACKBOARD_API_KEY`，通过部署平台的密钥管理能力注入，不要写入 `.env` 后提交。
2. 指定实际前端域名到 `CORS_ORIGINS`。
3. 将 SQLite 数据目录挂载到持久化卷，并定期备份。
4. 在反向代理层终止 TLS、配置访问日志与请求大小限制。
5. 采集 `/metrics`，关注任务积压、冲突数量、事件总线丢弃计数与 HTTP 延迟直方图；不要将业务 ID 或 query 参数作为指标标签。

## 健康检查与观测

- `GET /`：进程与版本信息。
- `GET /api/stats`：业务实体统计。
- `GET /metrics`：Prometheus 文本指标。
- `GET /docs`：OpenAPI/Swagger 页面。

### 关联 ID 与指标

- HTTP 处理器正常返回的响应含 `X-Request-ID`。调用方可传入安全的 ASCII 标识（字母、数字、`.`、`_`、`-`，最长 128 字符）用于工单关联；非法值会被服务端替换为 UUID。
- 由该请求触发的领域事件会携带同一 `trace_id`，并在 `/ws` 推送中暴露。日志、事件存档或客户端反馈应保留这个值，便于从请求定位到任务、条目或冲突事件。
- `blackboard_http_requests_total` 和 `blackboard_http_request_duration_seconds` 按 `method`、路由模板、`status` 聚合。它们是单进程内存指标：进程重启会清零，多副本部署应由 Prometheus 在采集端汇总。
- `/metrics` 的当前抓取请求会在响应发出后才入账，因此该响应快照不含本次抓取本身。
