"""The version is written once, in pyproject.toml (`app.core.config.VERSION`): the lock, the image tag and the image's
version label in docker-compose.yml must say the same."""

import re
import tomllib

from app.core.config import BACKEND_DIR, REPO_DIR, VERSION


def test_lock_has_the_project_version():
    lock = tomllib.loads((BACKEND_DIR / "uv.lock").read_text(encoding="utf-8"))
    assert next(p["version"] for p in lock["package"] if p["name"] == "coi-formularze") == VERSION


def test_compose_tags_the_image_with_the_project_version():
    compose = (REPO_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.findall(r"image: coi-formularze:(\S+)", compose) == [VERSION]
    assert re.findall(r"VERSION: (\S+)", compose) == [VERSION]
