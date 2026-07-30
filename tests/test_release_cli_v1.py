import json

from typer.testing import CliRunner

from clu_latent.cli import app
from clu_latent.doctor import run_doctor_checks

runner = CliRunner()


def test_version_surface_has_package_profile_and_python():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert "clulatent 1.0.0" in result.stdout
    assert "clulatent.profile.v1" in result.stdout
    assert "Python " in result.stdout
    assert "/Users/" not in result.stdout


def test_doctor_has_required_pass_warning_fail_categories():
    report = run_doctor_checks()
    names = {check.name for check in report.checks}
    assert {
        "Python",
        "ffmpeg",
        "ffprobe",
        "Pillow visual extra",
        "Temporary directory",
        "Browser opener",
        "Conformance fixtures",
    } <= names
    assert {check.level for check in report.checks} <= {"PASS", "WARNING", "FAIL"}
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    assert all("level" in check for check in json.loads(result.stdout)["checks"])


def test_welcome_is_concise_and_has_first_run_demo_docs_safety():
    result = runner.invoke(app, ["welcome"])
    assert result.exit_code == 0
    assert "build-video" in result.stdout
    assert "clulatent open" in result.stdout
    assert "clulatent demo" in result.stdout
    assert "github.com/DOGESON-j/clu-latent" in result.stdout
    assert "not media meaning" in result.stdout


def test_demo_is_offline_and_compatible(tmp_path):
    output = tmp_path / "demo.clulatent"
    result = runner.invoke(
        app, ["demo", "--no-browser", "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert output.is_file()
    assert runner.invoke(app, ["archive", "verify", str(output)]).exit_code == 0
    assert runner.invoke(app, ["profile", "verify", str(output)]).exit_code == 0
