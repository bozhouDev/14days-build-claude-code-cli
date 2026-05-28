# Day 2 Start

This snapshot was copied from `packages/day-01-hello-agent`.

Use it as the Day 2 starting point, then follow `docs/day-02-real-model-tool-calling.md` to replace `MockProvider` with a real model provider and tool calling.

## Run

```bash
uv run agent-code
```

Expected output:

```txt
hello from template
```

## Test

```bash
uv run --extra test pytest
```
