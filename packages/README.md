# Packages

`packages/day-*` 是每天的参考快照。每个 day 保留自己的 `pyproject.toml` 和 `uv.lock`，这样可以单独验证。

本地开发时可以让这些快照共用一个虚拟环境，减少重复安装：

```bash
cd /Users/xc/my/7days-build-claude-code-cli
uv venv packages/.venv
export UV_PROJECT_ENVIRONMENT="$PWD/packages/.venv"
```

之后切到某一天运行：

```bash
uv run --project packages/day-01-hello-agent agent-code "hello"
uv run --project packages/day-02-real-model-tool-calling agent-code "hello"
```

切换 day 时让 `uv run --project ...` 自己同步依赖即可。`packages/.venv` 只是本地缓存，不要提交。

测试某一天时，建议进入对应目录再跑，避免 pytest 从仓库根目录收集到多个 day 的同名测试文件：

```bash
cd packages/day-02-real-model-tool-calling
UV_PROJECT_ENVIRONMENT="../.venv" uv run pytest
```
