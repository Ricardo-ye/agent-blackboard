# 性能基线与报告

性能脚本在临时 SQLite 数据库和独立端口上启动服务，不污染开发库。它测量单请求延迟、不同并发下的任务创建、单条/批量条目写入、读取吞吐、Python 分配内存和 HTTP 到 WebSocket 的端到端推送延迟。

```powershell
# 仅在终端打印结果（原有行为）
.\python\python.exe tests\run_performance.py

# 同时生成结构化 JSON 报告
.\python\python.exe tests\run_performance.py --report reports\benchmarks\local.json
```

报告包含生成时间、Python 与操作系统版本、测试基地址以及各项基准数据。`reports/benchmarks/*.json` 默认被 Git 忽略：基线依赖 CPU、磁盘、Python 和 SQLite 文件系统，不应把一次本机运行误写成通用生产指标。

## 解释原则

- 单条写入与批量写入应分开比较；批量端点以一次提交减少 SQLite 提交次数，不能代表单条写入能力。
- 性能脚本遇到 HTTP 非 2xx、批量返回数量不一致或 2 秒内未收到 WebSocket 推送会失败，不再将失败请求静默记为“慢”。
- CI 可先保存报告作为趋势证据；不要直接使用绝对吞吐阈值阻断 CI。若要设门槛，应在固定硬件上连续采样，采用相对基线和人工复核。
- `tracemalloc` 只反映 Python 分配，不等于操作系统进程 RSS 或容器内存上限。
