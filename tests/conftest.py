import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import pytest  # noqa: E402

from voice_claude import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def russian_locale():
    """The suite below was written in Russian, and Russian is what it checks.

    The shell now answers in four languages and the deployment default is
    English, so every test that asserts a spoken phrase has to say which
    locale it means. Rather than repeat that in two hundred places, the whole
    legacy suite declares it once here; `test_i18n.py` covers the rest.
    """
    i18n.use("ru")
    yield
    i18n.reset()
