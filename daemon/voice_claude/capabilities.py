"""Что эта машина умеет — и откуда об этом знает Claude Code.

Инструмент, о котором модель не знает, — инструмент, которым она не
воспользуется. На сервере может стоять ffmpeg, headless-браузер и ключ к
генератору картинок, но если в сессию об этом не сказали, она ответит «я не
умею делать видео» и будет по-своему права: она действительно не знает, что
ffmpeg в двух шагах.

Поэтому здесь два дела разом. Первое — посмотреть, что на машине правда есть.
Второе — сложить из увиденного приписку к системному промпту, которая говорит
сессии ровно то, что есть, и ничего сверх. Обещать несуществующее хуже, чем
молчать: модель потратит ход, упрётся в «command not found» и объяснит это
человеку, который в этот момент идёт по улице и слышит ответ одним ухом.

Приписка написана по-английски. Всё остальное, что говорит оболочка, живёт в
каталоге на четырёх языках, потому что это слышит человек. Это — инструкция
модели, её человек не слышит никогда, а язык ответа задаётся отдельной
строкой и от языка инструкции не зависит.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Имя -> (чем звать, что это даёт). Порядок вариантов важен: берётся первый
# найденный, поэтому современное имя стоит раньше устаревшего.
_TOOLS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("video", ("ffmpeg",),
     "ffmpeg — cut, convert, concatenate, overlay, build a video from frames, "
     "extract stills, strip or add audio"),
    ("probe", ("ffprobe",), "ffprobe — read duration, codecs and resolution"),
    ("images", ("magick", "convert"),
     "ImageMagick — resize, crop, composite, annotate, convert between formats"),
    ("browser", ("chromium", "chromium-browser", "google-chrome"),
     "headless Chromium — screenshot any URL, render HTML you wrote to PNG or PDF; "
     "this is also how you turn a drawing you made in SVG or HTML into a real image"),
    ("node", ("node",), "node and npm — scaffold and build real web projects"),
    ("search", ("rg",), "ripgrep"),
    ("docs", ("pandoc",), "pandoc — convert between document formats"),
    ("git", ("git",), "git"),
)


@dataclass(frozen=True)
class Capabilities:
    """Снимок машины: что стоит, куда класть результат, чем думать."""

    workspace: Path
    public_dir: Path | None = None
    public_url: str | None = None
    model: str | None = None
    effort: str | None = None
    skills: str | list[str] | None = None
    mcp_config: Path | None = None
    image_command: str | None = None
    video_command: str | None = None
    found: dict[str, str] = field(default_factory=dict)

    @property
    def can_publish(self) -> bool:
        return self.public_dir is not None and bool(self.public_url)

    def briefing(self) -> str:
        """Приписка к системному промпту Claude Code."""
        lines = [
            "## This session is spoken, not typed",
            "",
            "You are running as a long-lived session on a server. The person "
            "talks to you through a headset, often walking, with the phone in "
            "a pocket. They hear a one-to-three sentence summary of what you "
            "say, never the full output, and they cannot see your screen.",
            "",
            "Two consequences, and they are not style advice:",
            "",
            "- **Finish the job.** They cannot look at a diff, skim a file or "
            "answer a clarifying question without breaking stride. Pick the "
            "sensible reading, do the whole task, and report what you did. Ask "
            "only when a wrong guess would be expensive and irreversible.",
            "- **Leave the result somewhere reachable.** Anything you make "
            "that is not text — a page, an image, a video, a report — has to "
            "end up behind a link or a file they can open later. Output that "
            "exists only in your reply is output they will never see.",
        ]

        if self.can_publish:
            lines += [
                "",
                "## Publishing",
                "",
                f"Anything you copy into `{self.public_dir}` is served at "
                f"{self.public_url} over the public internet. A file at "
                f"`{self.public_dir}/demo/index.html` is reachable at "
                f"{self.public_url.rstrip('/')}/demo/. Use it for every "
                "artifact: sites, images, videos, reports. Finish by saying "
                "the link in one short sentence.",
                "",
                "It is public and unlisted — anything with a secret in it does "
                "not go there.",
            ]
        else:
            lines += [
                "",
                "## Publishing",
                "",
                "No public directory is configured on this machine, so you "
                "have no way to hand over a file by link. Say so when a task "
                "would need one instead of building something the person "
                "cannot open.",
            ]

        if self.found:
            lines += ["", "## On this machine", ""]
            lines += [f"- {what}" for what in self.found.values()]

        if self.image_command:
            lines += [
                "",
                "## Generated images and video",
                "",
                f"`{self.image_command}` generates an image from a text "
                "prompt. Run it with `--help` the first time to see its "
                "arguments.",
            ]
            if self.video_command:
                lines.append(
                    f"`{self.video_command}` does the same for video.")
        else:
            lines += [
                "",
                "## Generated images and video",
                "",
                "There is no image or video generator configured here, so you "
                "cannot conjure a photograph from a description. Say that "
                "plainly rather than producing a placeholder and calling it "
                "the thing that was asked for.",
            ]
            if self.found.get("browser") or self.found.get("video"):
                made = []
                if self.found.get("browser"):
                    made.append(
                        "draw it yourself — SVG or HTML rendered to PNG through "
                        "headless Chromium covers diagrams, charts, posters and "
                        "mock-ups")
                if self.found.get("video"):
                    made.append(
                        "assemble and edit video with ffmpeg — frames into a "
                        "clip, cuts, overlays, captions")
                lines += [
                    "",
                    "What you can do instead, and should offer when it fits: "
                    + "; ".join(made) + ".",
                ]

        lines += [
            "",
            "## Working at full stretch",
            "",
            "- Web search and web fetch are on. Use them rather than answering "
            "from memory on anything that could have changed.",
            "- The Task tool runs subagents in parallel. Work that fans out — "
            "reading many sources, checking many files, trying several "
            "approaches — goes to subagents, and you keep the conclusion.",
            "- Long jobs are fine. Nobody is watching a progress bar; a good "
            "answer in four minutes beats a hedge in twenty seconds.",
        ]
        return "\n".join(lines)


def _first(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _path(value: str | None) -> Path | None:
    """Путь из окружения — или None, если его не задали."""
    value = (value or "").strip()
    return Path(value).expanduser() if value else None


def detect(workspace: str | Path, environ: dict[str, str] | None = None) -> Capabilities:
    """Посмотреть, что на машине есть на самом деле."""
    env = os.environ if environ is None else environ
    workspace = Path(workspace).expanduser()

    found: dict[str, str] = {}
    for key, names, description in _TOOLS:
        if _first(*names):
            found[key] = description

    public_dir = _path(env.get("PUBLIC_DIR"))
    public_url = (env.get("PUBLIC_URL") or "").strip() or None
    # Каталог без адреса — это каталог, ссылку на который назвать нечем;
    # адрес без каталога — обещание, которое некуда положить. Нужны оба.
    if not (public_dir and public_url):
        public_dir, public_url = None, None

    mcp_config = _path(env.get("MCP_CONFIG"))
    if mcp_config is not None and not mcp_config.is_file():
        mcp_config = None

    skills: str | list[str] | None = (env.get("CODE_SKILLS") or "all").strip() or None
    if skills not in (None, "all"):
        skills = [name.strip() for name in str(skills).split(",") if name.strip()]

    return Capabilities(
        workspace=workspace,
        public_dir=public_dir,
        public_url=public_url,
        model=(env.get("CODE_MODEL") or "").strip() or None,
        effort=(env.get("CODE_EFFORT") or "").strip() or None,
        skills=skills,
        mcp_config=mcp_config,
        image_command=_first("voice-imagine") or None,
        video_command=_first("voice-animate") or None,
        found=found,
    )
