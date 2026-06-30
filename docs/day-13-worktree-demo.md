# Day 13：Worktree 隔离 + 端到端 Demo 1.0

前 12 天，`agent-code` 已经能读代码、改文件、跑命令、做计划、用 skill、启动 subagent、协调 teammate。

但还有一个现实问题：Agent 直接改主分支很危险。它可以改错文件，可以跑到一半被中断，也可能在你已有未提交改动时制造一堆冲突。

今天做 Worktree 隔离：让 Agent 进入一个独立 git worktree，在那里修 bug、跑测试。确认没问题后，再 merge 回主目录。

跑完之后你会看到：

- `enter_worktree(branch)` 会先检查主目录是否干净，再创建 `.worktrees/<branch>`。
- harness 的 cwd 会真的切到 worktree，文件工具、bash、subagent 都跟着新 cwd 走。
- `worktree-state` 会写进 session JSONL，`--resume` 后还能回到隔离目录。
- `exit_worktree("merge")` 成功后合回主分支并清理 worktree；冲突时保留现场。
- v3 会串起一个端到端 demo：plan → subagent → file_edit → pytest → merge。

代码约 520 行，新增约 300 行。

今天分三版：

1. v1：`enter_worktree`，创建隔离目录并切 cwd。
2. v2：`exit_worktree`，支持 `merge / discard / keep`。
3. v3：端到端修复 `examples/buggy-python-project` 的 failing test。

## 起手：今天的起点

Day 12 的 `agent-code` 已经有 `RuntimeState`，但当前 cwd 仍然多半是 `cli.py` 里的局部变量：

```txt
resolved_cwd = cwd.resolve()
run_agent(..., cwd=resolved_cwd)
```

这在 worktree 里会卡住：工具里就算创建了 `.worktrees/fix-bug`，下一轮 `run_agent()` 还是用旧 `resolved_cwd`。

所以 Day 13 的第一件事不是写 git 命令，而是把 cwd 提升到运行态：

```txt
RuntimeState.cwd
RuntimeState.original_cwd
RuntimeState.worktree
```

只要 `ToolContext.cwd` 对了，`read_file`、`file_edit`、`bash`、subagent 都不用各自改路径。

## v1：进入 worktree，不只是返回一段文本

`enter_worktree` 要做四件事：

1. 主目录必须干净。
2. 分支名必须 sanitize。
3. `git worktree add` 创建隔离目录。
4. 更新 `RuntimeState.cwd`，让下一轮工具真的在 worktree 里跑。

### 1.1 新增 `agent_code/worktree.py`

```python
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorktreeState:
    original_cwd: Path
    worktree_path: Path
    branch: str
    from_branch: str

    def to_dict(self) -> dict[str, str]:
        return {
            "original_cwd": str(self.original_cwd),
            "worktree_path": str(self.worktree_path),
            "branch": self.branch,
            "from_branch": self.from_branch,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "WorktreeState":
        return cls(
            original_cwd=Path(data["original_cwd"]),
            worktree_path=Path(data["worktree_path"]),
            branch=data["branch"],
            from_branch=data["from_branch"],
        )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=60,
    )


def sanitize_branch(raw: str) -> str:
    value = raw.strip().replace(" ", "-")
    value = re.sub(r"[^a-zA-Z0-9._-]", "-", value)
    value = re.sub(r"-+", "-", value).strip(".-")
    if not value:
        raise ValueError("branch name is empty after sanitize")
    if value in (".", "..") or ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"unsafe branch name: {raw}")
    return value[:64]


def ensure_clean_worktree(cwd: Path) -> None:
    proc = _git(cwd, "status", "--porcelain")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git status failed")
    if proc.stdout.strip():
        raise RuntimeError("working tree is dirty; commit or stash before enter_worktree")


def enter_worktree(branch: str, from_branch: str, state, session) -> str:
    if state.worktree is not None:
        return f"error: already in worktree: {state.worktree.worktree_path}"

    original = state.cwd
    ensure_clean_worktree(original)
    safe_branch = sanitize_branch(branch)
    base = original / ".worktrees"
    base.mkdir(exist_ok=True)
    path = base / safe_branch

    proc = _git(original, "worktree", "add", "-b", safe_branch, str(path), from_branch)
    if proc.returncode != 0:
        return f"error: {proc.stderr.strip()}"

    state.worktree = WorktreeState(
        original_cwd=original,
        worktree_path=path,
        branch=safe_branch,
        from_branch=from_branch,
    )
    state.cwd = path
    if session is not None:
        session.append_worktree_state(state.worktree)
    return f"entered worktree: {path}\nbranch: {safe_branch}"
```

进入前检查 dirty tree 是刻意严格。主目录有未提交改动时，再 merge worktree 分支会很难判断冲突来自哪里。

### 1.2 `RuntimeState` 加 cwd/worktree

打开 `agent_code/runtime.py`：

```python
from pathlib import Path

if TYPE_CHECKING:
    from .worktree import WorktreeState
```

`RuntimeState` 里新增：

```python
    cwd: Path = field(default_factory=Path.cwd)
    original_cwd: Path = field(default_factory=Path.cwd)
    worktree: "WorktreeState | None" = None
```

### 1.3 `cli.py` 改成读 `state.cwd`

交互模式创建 state 后：

```python
state = RuntimeState(...)
state.cwd = resolved_cwd
state.original_cwd = resolved_cwd
```

`run_turn()` 改成：

```python
run_agent(
    line,
    turn_provider,
    tools,
    max_steps=max_steps,
    cwd=state.cwd,
    state=state,
    session=session,
    system_prompt=system_prompt,
)
```

`make_slash_context()` 也改：

```python
cwd=state.cwd
```

one-shot `run_once()` 同样设置：

```python
state.cwd = cwd
state.original_cwd = cwd
```

### 1.4 session 记录 worktree-state

打开 `agent_code/session.py`，`history` 解析时先跳过非消息行：

```python
if "role" not in data or "content" not in data:
    continue
```

新增两个方法：

```python
def append_worktree_state(self, state) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload = state.to_dict() if state is not None else None
    with open(self.file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(
            {"type": "worktree-state", "worktree": payload, "timestamp": now},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n")


def load_worktree_state(self):
    from .worktree import WorktreeState

    latest = None
    if not self.file_path.exists():
        return None
    for line in self.file_path.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") == "worktree-state":
            latest = data.get("worktree")
    if latest is None:
        return None
    state = WorktreeState.from_dict(latest)
    if not state.worktree_path.exists():
        self.append_worktree_state(None)
        return None
    return state
```

resume 时，在 `cli.py` 创建 `RuntimeState` 后：

```python
if session is not None:
    restored = session.load_worktree_state()
    if restored is not None:
        state.worktree = restored
        state.original_cwd = restored.original_cwd
        state.cwd = restored.worktree_path
```

路径不存在时 fail-closed：回到原 cwd，不假装还在隔离目录。

### 1.5 注册 `enter_worktree`

打开 `tools.py`：

```python
def enter_worktree_tool(args: dict[str, Any], ctx: ToolContext) -> str:
    from .worktree import enter_worktree

    state = ctx.runtime_state
    if state is None:
        return "error: no runtime state"
    branch = str(args.get("branch", "")).strip()
    from_branch = str(args.get("from_branch", "HEAD")).strip() or "HEAD"
    if not branch:
        return "error: missing required argument 'branch'"
    session = getattr(state, "session", None)
    return enter_worktree(branch, from_branch, state, session)
```

为了让工具能拿到 session，`cli.py` 创建 state 后加：

```python
state.session = session
```

注册：

```python
registry.register(Tool(
    name="enter_worktree",
    description="Create a git worktree and switch the harness cwd into it.",
    run=enter_worktree_tool,
    parameters={
        "type": "object",
        "properties": {
            "branch": {"type": "string"},
            "from_branch": {"type": "string", "default": "HEAD"},
        },
        "required": ["branch"],
    },
))
```

权限里把 `enter_worktree` 放到 ask 或 low-risk write。建议默认 ask，因为它会跑 git 命令并创建目录。

跑验证：

```bash
$ git status --porcelain
# 必须没有输出

$ uv run agent-code --permission-mode acceptEdits "调用 enter_worktree，branch=fix-demo"
tool_call: enter_worktree {'branch': 'fix-demo'}
final: entered worktree: .../.worktrees/fix-demo
```

再问：

```bash
> 用 bash 跑 pwd
tool_call: bash {'command': 'pwd'}
final: .../.worktrees/fix-demo
```

如果 `pwd` 仍然是主目录，说明 `run_turn()` 还在用旧 `resolved_cwd`，没有切到 `state.cwd`。

## v2：退出 worktree，支持 merge / discard / keep

进入只是半边。退出时要明确三种动作：

```txt
keep     切回主目录，保留 worktree 和分支
discard  删除 worktree 和分支，需要强确认
merge    合回主分支，成功后清理；冲突时保留现场
```

### 2.1 `exit_worktree`

在 `worktree.py` 里追加：

```python
def _remove_worktree(original: Path, wt: WorktreeState, force: bool = False) -> str | None:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(wt.worktree_path))
    proc = _git(original, *args)
    if proc.returncode != 0:
        return proc.stderr.strip()
    proc = _git(original, "branch", "-D", wt.branch)
    if proc.returncode != 0:
        return proc.stderr.strip()
    return None


def exit_worktree(action: str, state, session) -> str:
    wt = state.worktree
    if wt is None:
        return "error: not in a worktree"
    action = action.strip()
    original = wt.original_cwd

    if action == "keep":
        state.cwd = original
        state.worktree = None
        if session is not None:
            session.append_worktree_state(None)
        return f"left worktree and kept branch: {wt.branch}"

    if action == "discard":
        err = _remove_worktree(original, wt, force=True)
        if err:
            return f"error: {err}"
        state.cwd = original
        state.worktree = None
        if session is not None:
            session.append_worktree_state(None)
        return f"discarded worktree: {wt.branch}"

    if action == "merge":
        proc = _git(original, "merge", "--no-ff", wt.branch)
        if proc.returncode != 0:
            return (
                "error: merge failed; worktree kept for manual resolution\n"
                + (proc.stderr.strip() or proc.stdout.strip())
            )
        err = _remove_worktree(original, wt, force=False)
        if err:
            return f"merged but cleanup failed: {err}"
        state.cwd = original
        state.worktree = None
        if session is not None:
            session.append_worktree_state(None)
        return f"merged and removed worktree: {wt.branch}"

    return "error: action must be merge, discard, or keep"
```

merge 冲突时不清理 worktree。现场留着，人才知道去哪里处理。

### 2.2 注册 `exit_worktree`

`tools.py`：

```python
def exit_worktree_tool(args: dict[str, Any], ctx: ToolContext) -> str:
    from .worktree import exit_worktree

    state = ctx.runtime_state
    if state is None:
        return "error: no runtime state"
    action = str(args.get("action", "")).strip()
    if action not in ("merge", "discard", "keep"):
        return "error: action must be merge, discard, or keep"
    session = getattr(state, "session", None)
    return exit_worktree(action, state, session)
```

注册：

```python
registry.register(Tool(
    name="exit_worktree",
    description="Exit the active git worktree. action is merge, discard, or keep.",
    run=exit_worktree_tool,
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["merge", "discard", "keep"]},
        },
        "required": ["action"],
    },
))
```

权限建议：

- `keep` 可以 allow。
- `merge` 需要 ask。
- `discard` 需要强确认。

教学版最小做法：`exit_worktree` 统一走 ask，确认 UI 里显示 action。

### 2.3 跑验证

```bash
> 调用 enter_worktree branch=try-keep
...
> 调用 exit_worktree action=keep
tool_call: exit_worktree {'action': 'keep'}
final: left worktree and kept branch: try-keep
> 用 bash 跑 pwd
final: /your/main/project
```

discard 验证要小心，它会删除 branch：

```bash
> 调用 enter_worktree branch=try-discard
> 调用 exit_worktree action=discard
```

merge 验证留到 v3 的 demo。

## v3：端到端修一个 failing test

现在用一个小项目把前 12 天串起来。先准备 demo。

### 3.1 创建 buggy project

在你的 `agent-code` 项目里执行：

```bash
mkdir -p examples/buggy-python-project/src/buggy_calc examples/buggy-python-project/tests
cat > examples/buggy-python-project/pyproject.toml <<'EOF'
[project]
name = "buggy-python-project"
version = "0.1.0"
requires-python = ">=3.10"

[tool.pytest.ini_options]
pythonpath = ["src"]
EOF

cat > examples/buggy-python-project/src/buggy_calc/__init__.py <<'EOF'
from .calculator import divide

__all__ = ["divide"]
EOF

cat > examples/buggy-python-project/src/buggy_calc/calculator.py <<'EOF'
def divide(a: int, b: int) -> float:
    return a / (b + 1)
EOF

cat > examples/buggy-python-project/tests/test_calculator.py <<'EOF'
from buggy_calc import divide


def test_divide():
    assert divide(6, 2) == 3
EOF

cat > examples/buggy-python-project/README.md <<'EOF'
# Buggy Python Project

Run:

```bash
uv run pytest
```

Expected fix: `divide(6, 2)` should return `3`.
EOF
```

验证它确实失败：

```bash
$ cd examples/buggy-python-project
$ uv add --dev pytest
$ uv run pytest
FAILED tests/test_calculator.py::test_divide
```

回到主项目根目录：

```bash
cd ../..
git status --short
```

如果这些 demo 文件还没提交，`enter_worktree` 会因为 dirty tree 拒绝。你可以先提交 demo 素材，或者在自己的练习仓库里做。

### 3.2 端到端任务 prompt

启动：

```bash
uv run agent-code --permission-mode plan
```

输入：

```txt
在隔离 worktree 里修复 examples/buggy-python-project 的 failing test。
流程必须是：
1. 调用 enter_worktree，branch=fix-buggy-divide
2. 先用 plan mode 给出计划，等我批准
3. 用 debugger subagent 分析失败原因
4. 修复代码
5. 在 examples/buggy-python-project 里跑 uv run pytest
6. 通过后调用 exit_worktree action=merge
```

预期 trace 大概是：

```txt
tool_call: enter_worktree {'branch': 'fix-buggy-divide'}
tool_call: exit_plan_mode {'plan_summary': '...'}
# 用户批准
tool_call: agent {'agent_name': 'debugger', ...}
tool_call: read_file {'path': 'examples/buggy-python-project/src/buggy_calc/calculator.py'}
tool_call: file_edit {'file_path': 'examples/.../calculator.py', ...}
tool_call: bash {'command': 'cd examples/buggy-python-project && uv run pytest'}
tool_call: exit_worktree {'action': 'merge'}
final: ...
```

关键不是 trace 一模一样，而是这几个验收：

```txt
1. enter 后 pwd 在 .worktrees/fix-buggy-divide。
2. pytest 在 worktree 里从失败变通过。
3. merge 后主目录的 calculator.py 已修复。
4. .worktrees/fix-buggy-divide 被清理。
5. git status 能看见 merge commit 或合并后的干净状态。
```

### 3.3 为什么所有工具会自动跟着 worktree

因为前面几天所有工具都只看 `ToolContext.cwd`：

```txt
read_file -> resolve_in_cwd(ctx.cwd, path)
file_edit -> resolve_in_cwd(ctx.cwd, path)
bash      -> subprocess.run(..., cwd=ctx.cwd)
agent     -> subagent_runner(..., cwd=ctx.cwd)
```

Day 13 只要把 `state.cwd` 切到 worktree，再让 `run_agent(..., cwd=state.cwd)`，整个工具面就会跟着走。

## 收尾：今天改了哪些文件

今天新增一个文件：

```txt
agent_code/worktree.py
```

今天改了五个已有文件：

```txt
agent_code/runtime.py      cwd / original_cwd / worktree
agent_code/session.py      worktree-state 元数据
agent_code/cli.py          run_agent 和 slash context 改读 state.cwd
agent_code/tools.py        enter_worktree / exit_worktree
agent_code/permissions.py  worktree 工具审批
```

教程里还让你在练习项目里创建：

```txt
examples/buggy-python-project/
```

## 手动 trace 一遍

### 路径一：enter_worktree

```txt
1. 模型调用 enter_worktree(branch="fix-demo")。
2. worktree.py 检查 git status --porcelain。
3. sanitize branch。
4. git worktree add -b fix-demo .worktrees/fix-demo HEAD。
5. RuntimeState.cwd = .worktrees/fix-demo。
6. session 写 worktree-state。
7. 下一轮所有工具从新 cwd 开始。
```

### 路径二：resume

```txt
1. session JSONL 里有最后一条 worktree-state。
2. --resume 读取它。
3. 如果 worktree_path 还存在，state.cwd 恢复到 worktree。
4. 如果路径不存在，写回 null，回主 cwd。
```

### 路径三：merge

```txt
1. exit_worktree(action="merge") 在主目录执行 git merge --no-ff branch。
2. 成功后 git worktree remove path。
3. 删除临时 branch。
4. state.cwd 回 original_cwd。
5. session 写 worktree-state=null。
6. 冲突时不清理，返回错误，让人手动处理。
```

## 今天有了什么

- **会话级 worktree**：一次只允许一个 active worktree。
- **cwd 运行态**：工具不是各自改路径，而是统一读 `RuntimeState.cwd`。
- **dirty tree 预检**：进入前主目录必须干净。
- **session 恢复**：`worktree-state` 让中断后的会话回到隔离目录。
- **merge/discard/keep**：退出动作明确，冲突不清现场。
- **端到端 demo**：前 12 天能力第一次串成修 bug 流程。

## 常见问题

### `enter_worktree` 报 working tree is dirty

先运行：

```bash
git status --short
```

把已有改动 commit 或 stash。Day 13 故意要求主目录干净，这样 merge 时风险更小。

### 进入 worktree 后工具还是读主目录

检查 `cli.py`：`run_agent(..., cwd=state.cwd)` 和 `SlashContext(cwd=state.cwd)` 是否都改了。只改工具函数不够，下一轮仍会用旧闭包变量。

### `exit_worktree("merge")` 冲突了怎么办

不要删除 worktree。它会保留 `.worktrees/<branch>` 和分支。你可以手动解决冲突，再决定 merge 或 keep。

### 为什么不是每个 subagent 自己开 worktree

今天做的是会话级 worktree。父 Agent、subagent、bash 都在同一个隔离目录里工作。每个 subagent 再开 worktree 会让路径和 merge 归属变复杂，先不做。

## 课后挑战

1. `enter_worktree` 支持复用已有 branch。
2. 给 `exit_worktree("discard")` 做强确认短语，例如必须输入 branch 名。
3. `/context` 显示当前是否在 worktree。
4. merge 前自动跑一条用户配置的验证命令。
5. worktree 目录不存在时，在 resume 提示用户恢复或丢弃状态。

## 思考题

1. **为什么 worktree 切换要放进 `RuntimeState.cwd`，而不是让每个工具自己判断？**
2. **进入前 dirty tree 预检解决了什么问题？**
3. **`merge` 冲突时为什么不能清理 worktree？**
4. **`Session.history` 为什么要跳过 `worktree-state` 元数据行？**

## 下一天

今天把修改隔离到了 git worktree。最后一天接 MCP：工具不再只能写在 `tools.py` 里，而是可以从外部 server 动态接入。工具一多，还要靠 ToolSearch 按需发现，而不是把所有 schema 都塞进首轮 prompt。
