# 开发指南

## 本地质量门禁

```bash
python -m compileall -q app main.py config.py
npx --yes pyright --project pyrightconfig.json
python -m pytest -q
python tests/e2e_verify.py
python tests/ws_verify.py
python tests/runtime_probe.py
python tests/run_performance.py --report reports/benchmarks/local.json
```

独立验证脚本会启动隔离端口与临时数据库，不会污染开发库。浏览器 UI 冒烟依赖本机 Edge 或 Chrome，
属于本地可选检查；CI 使用 API、WebSocket 与运行时冒烟覆盖可移植链路。

## 模块边界

- `app/api/`：HTTP 和 WebSocket 边界，负责参数校验和状态码。
- `app/*_registry.py`、`blackboard.py`、`task_coordinator.py`：领域服务。
- `storage.py`：存储抽象与 SQLite 实现。
- `event_bus.py`：域事件分发。
- `web/`：零构建控制台，不依赖后端私有实现。

涉及公开 API、状态机、规则语义或数据库结构的变更，请先补充测试，并在 Pull Request 中说明兼容性影响。
性能报告默认不提交，因为硬件和文件系统会影响结果；解释方式见[性能基线与报告](benchmarking.md)。
