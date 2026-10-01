# 贡献指南

感谢你对智能体动态协作黑板系统的关注。提交改动前，请先阅读本指南和
[架构设计](ARCHITECTURE.md)，并尽量让每次变更保持小而聚焦。

## 开发环境

项目要求 Python 3.11 或更高版本。

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 本地验证

提交前至少运行以下命令：

```bash
python -m compileall -q app main.py config.py
python -m pytest -q
```

涉及浏览器界面、WebSocket、并发或性能的改动，还应运行 README 中对应的专项验证脚本。

## 提交变更

1. 先创建或认领 Issue，说明问题、预期行为和建议方案。
2. 从 `main` 创建聚焦单一目标的分支。
3. 沿用现有代码风格，并为行为变更补充测试和文档。
4. 使用清晰的提交信息，例如 `fix: prevent duplicate task assignment`。
5. 发起 Pull Request，说明改动动机、验证方式和潜在兼容性影响。

请勿提交 API 密钥、数据库、日志、浏览器配置、虚拟环境或嵌入式运行时。

## 问题报告

安全漏洞请不要提交公开 Issue，而应按照 [安全政策](SECURITY.md) 私下报告。
参与社区协作时请遵守 [行为准则](CODE_OF_CONDUCT.md)。
