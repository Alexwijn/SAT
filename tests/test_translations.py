import json
from pathlib import Path
from string import Formatter

import pytest

from custom_components.sat.types import ControlSignal

TRANSLATIONS = Path(__file__).parent.parent / "custom_components" / "sat" / "translations"
LANGUAGES = sorted(path.stem for path in TRANSLATIONS.glob("*.json"))


def _texts(language: str) -> dict[str, str]:
    texts = {}

    def _flatten(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                _flatten(item, f"{path}.{key}")
        else:
            texts[path] = value

    _flatten(json.loads((TRANSLATIONS / f"{language}.json").read_text()), "")
    return texts


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_language_has_the_keys_of_english(language):
    assert _texts(language).keys() == _texts("en").keys()


@pytest.mark.parametrize("language", LANGUAGES)
def test_issues_keep_the_placeholders_of_english(language):
    english, translated = _texts("en"), _texts(language)

    for key in (key for key in english if key.startswith(".issues.")):
        assert _placeholders(translated[key]) == _placeholders(english[key]), key


@pytest.mark.parametrize("signal", list(ControlSignal))
def test_every_control_signal_has_an_issue(signal):
    assert f".issues.missing_{signal}.title" in _texts("en")


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(text) if field}
