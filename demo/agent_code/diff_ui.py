from __future__ import annotations

# Day 5：render_diff 和 confirm_edit 的实现迁到了 prompt_ui.py。
# 这里保留 re-export，老 import 路径不受影响。
from .prompt_ui import confirm_edit, render_diff

__all__ = ["confirm_edit", "render_diff"]