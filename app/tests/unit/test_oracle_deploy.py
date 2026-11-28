"""Exercise deployment failure boundaries without contacting Docker or production."""

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Oracle deployment runs on Linux")


@pytest.mark.parametrize("failure", ["", "build", "pg_dump", "migrate", "up"])
def test_deployment_stops_at_failed_stage(tmp_path: Path, failure: str) -> None:
    root = tmp_path / "backend"
    root.mkdir()
    (root / ".env.production").write_text("TEST_ONLY=true\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    fake_docker = tools / "sudo"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$CALL_LOG"
case "$*" in
  *'ps --status running'*) printf 'postgres-id\n';;
  *' build') [[ ${FAILURE:-} != build ]];;
  *pg_dump*) [[ ${FAILURE:-} != pg_dump ]]; printf 'test-dump';;
  *pg_restore*) cat > /dev/null;;
  *'run --rm --no-deps migrate') [[ ${FAILURE:-} != migrate ]];;
  *' up '*) [[ ${FAILURE:-} != up ]];;
esac
"""
    )
    fake_docker.chmod(0o700)
    fake_curl = tools / "curl"
    fake_curl.write_text("#!/usr/bin/env bash\nprintf 'ready'\n")
    fake_curl.chmod(0o700)
    source = Path("deploy/oracle-deploy.sh").read_text()
    script = tmp_path / "deploy.sh"
    script.write_text(source.replace("root=/home/ubuntu/inkwell-backend", f"root='{root}'"))
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as package:
        for name in (
            "Dockerfile",
            "compose.production.yaml",
            "compose.https.yaml",
            "Caddyfile",
            "app/main.py",
        ):
            data = b"test-only\n"
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            package.addfile(entry, io.BytesIO(data))
    sha = "a" * 40
    result = subprocess.run(
        ["bash", str(script)],
        input=archive.getvalue(),
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{tools}:{os.environ['PATH']}",
            "CALL_LOG": str(tmp_path / "calls"),
            "FAILURE": failure,
            "SSH_ORIGINAL_COMMAND": f"deploy {sha}",
        },
        timeout=10,
        check=False,
    )
    calls = (tmp_path / "calls").read_text()
    release = next((root / "releases").iterdir())
    assert (release / "Dockerfile").stat().st_mode & 0o444 == 0o444
    assert (release / "app").stat().st_mode & 0o555 == 0o555
    assert (release / "app/main.py").stat().st_mode & 0o444 == 0o444
    if failure:
        assert result.returncode != 0
        assert not (root / ".deployed-commit").exists()
        if failure in ("build", "pg_dump"):
            assert "stop --timeout" not in calls
        if failure == "migrate":
            assert "stop --timeout" in calls
            assert " up " not in calls
    else:
        assert result.returncode == 0, result.stderr.decode()
        assert (root / ".deployed-commit").read_text().strip() == sha
        assert calls.index("pg_restore") < calls.index("stop --timeout")
        assert calls.index("run --rm --no-deps migrate") < calls.index(" up ")
