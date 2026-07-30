import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_required_public_documents_exist():
    required = [
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "SUPPORT.md",
        "ROADMAP.md",
        "GOVERNANCE.md",
        "CITATION.cff",
        "docs/QUICKSTART.md",
        "docs/FORMAT_V1.md",
        "docs/ARCHIVE_V1.md",
        "docs/SECURITY_MODEL.md",
        "docs/CONFORMANCE.md",
        "docs/COMPATIBILITY.md",
        "docs/CLI_REFERENCE.md",
        "docs/PYTHON_API.md",
        "docs/RELEASING.md",
        "docs/PYPI_PUBLISH_SETUP.md",
    ]
    assert all((ROOT / path).is_file() for path in required)


def test_distribution_metadata_is_public_v1():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = data["project"]
    assert project["name"] == "clu-latent"
    assert project["version"] == "1.0.0"
    assert project["requires-python"] == ">=3.10"
    assert project["license"] == "BSD-3-Clause"
    assert project["scripts"]["clulatent"] == "clu_latent.cli:app"
    assert "visual" in project["optional-dependencies"]
    assert "dev" in project["optional-dependencies"]
    assert data["tool"]["setuptools"]["package-data"]["clu_latent"]


def test_readme_quickstart_and_safety_are_exact():
    readme = (ROOT / "README.md").read_text()
    for text in [
        "pipx install clu-latent",
        "uv tool install clu-latent",
        "python -m pip install clu-latent",
        "uvx clu-latent --help",
        "clulatent build-video demo.mp4 -o demo.clulatent --profile v1",
        'clulatent ask demo.clulatent "What evidence exists around 17s?"',
        "CLULatent V1 compatibility certifies package/profile compatibility, not",
    ]:
        assert text in readme
    assert "sudo pip install" not in readme


def test_github_automation_is_parseable_and_has_required_gates():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    codeql = yaml.safe_load((ROOT / ".github/workflows/codeql.yml").read_text())
    assert ci["jobs"]
    assert release["jobs"]["publish-pypi"]["environment"] == "pypi"
    assert "PYPI_TRUSTED_PUBLISHING" in (
        ROOT / ".github/workflows/release.yml"
    ).read_text()
    assert codeql["jobs"]
    all_workflows = "\n".join(
        path.read_text() for path in (ROOT / ".github/workflows").glob("*.yml")
    )
    assert "PYPI_API_TOKEN" not in all_workflows
    assert "password:" not in all_workflows.lower()
