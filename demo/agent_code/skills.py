from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    allowed_tools: list[str] | None
    body: str
    path: Path


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """只解析教程需要的 frontmatter 子集，不引入 YAML 依赖。"""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text

    raw_frontmatter = text[4:end]
    body = text[end + len("\n---\n") :]
    fields: dict[str, str] = {}
    for line in raw_frontmatter.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields, body.strip()


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_allowed_tools(raw: str | None) -> list[str] | None:
    """三态：字段缺失=None；[]=禁止工具；[a,b]=只允许列表。"""
    if raw is None:
        return None
    value = raw.strip()
    if value == "[]":
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_unquote(part.strip()) for part in inner.split(",") if part.strip()]
    return [_unquote(value)]


class SkillLoader:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.skills_dir = cwd / ".agent" / "skills"
        self.warnings: list[str] = []

    def list(self) -> list[SkillMeta]:
        skills: list[SkillMeta] = []
        if not self.skills_dir.exists():
            return skills

        for skill_md in sorted(self.skills_dir.glob("*/SKILL.md")):
            skill = self._load_file(skill_md)
            if skill is not None:
                skills.append(skill)
        return skills

    def load(self, name: str) -> SkillMeta | None:
        for skill in self.list():
            if skill.name == name:
                return skill
        return None

    def render_list(self) -> str:
        skills = self.list()
        if not skills:
            return "(no skills found)"
        return "\n".join(f"{skill.name}  {skill.description}" for skill in skills)

    def render_available_skills(self) -> str:
        skills = self.list()
        if not skills:
            return ""
        lines = ["<available-skills>"]
        lines.extend(f"- {skill.name}: {skill.description}" for skill in skills)
        lines.append("</available-skills>")
        return "\n".join(lines)

    def _load_file(self, path: Path) -> SkillMeta | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            self.warnings.append(f"{path}: {exc}")
            return None

        fields, body = _split_frontmatter(text)
        name = _unquote(fields.get("name", "")).strip()
        description = _unquote(fields.get("description", "")).strip()
        if not name or not description:
            self.warnings.append(f"{path}: missing name or description")
            return None

        return SkillMeta(
            name=name,
            description=description,
            allowed_tools=_parse_allowed_tools(fields.get("allowed_tools")),
            body=body,
            path=path,
        )


@dataclass(frozen=True)
class OutputStyle:
    name: str
    description: str
    body: str
    path: Path


def list_output_styles(cwd: Path) -> list[OutputStyle]:
    styles_dir = cwd / ".agent" / "output-styles"
    if not styles_dir.exists():
        return []

    styles: list[OutputStyle] = []
    for path in sorted(styles_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, body = _split_frontmatter(text)
        name = _unquote(fields.get("name", path.stem)).strip()
        description = _unquote(fields.get("description", "")).strip()
        styles.append(OutputStyle(name=name, description=description, body=body, path=path))
    return styles


def load_output_style(cwd: Path, name: str) -> OutputStyle | None:
    for style in list_output_styles(cwd):
        if style.name == name:
            return style
    return None


def render_output_style(cwd: Path, name: str | None) -> str:
    if not name:
        return ""
    style = load_output_style(cwd, name)
    if style is None:
        return ""
    return f"<output-style name=\"{style.name}\">\n{style.body}\n</output-style>"
