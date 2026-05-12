import subprocess
import sys

from typer.testing import CliRunner

from covered import cli as mod

runner = CliRunner()


def test_script():  # For coverage (if __name__ == "__main__":)
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "run", mod.__file__, "--help"],
        capture_output=True,
        encoding="utf-8",
    )
    assert "Usage" in result.stdout
