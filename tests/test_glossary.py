"""Подсказка имён: распознаватель калечит названия, Claude чинит по контексту."""
from voice_claude import glossary


def test_project_names_are_collected(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.ts").write_text("", encoding="utf-8")
    (tmp_path / "src" / "session.ts").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")

    names = glossary.collect(tmp_path)
    assert "auth.ts" in names and "session.ts" in names and "src" in names


def test_noise_is_skipped(tmp_path):
    for junk in (".git", "node_modules", "__pycache__"):
        (tmp_path / junk).mkdir()
        (tmp_path / junk / "buried.ts").write_text("", encoding="utf-8")
    (tmp_path / "keep.py").write_text("", encoding="utf-8")
    (tmp_path / "image.png").write_text("", encoding="utf-8")

    names = glossary.collect(tmp_path)
    assert names == ["keep.py"]


def test_limit_is_respected(tmp_path):
    for i in range(80):
        (tmp_path / f"file{i}.py").write_text("", encoding="utf-8")
    assert len(glossary.collect(tmp_path, limit=10)) == 10


def test_own_words_come_first(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_GLOSSARY", "Aegis, Lamdan, Tailscale")
    (tmp_path / "keep.py").write_text("", encoding="utf-8")
    hint = glossary.for_workspace(tmp_path)
    assert "Aegis" in hint and "Tailscale" in hint and "keep.py" in hint
    assert hint.index("Aegis") < hint.index("keep.py")


def test_no_hint_for_an_empty_project(tmp_path):
    assert glossary.for_workspace(tmp_path / "нет-такого") == ""
