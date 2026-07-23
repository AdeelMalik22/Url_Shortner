import os
from pathlib import Path
import subprocess
import sys

import pytest

from app import observability


def test_multiprocess_directory_is_created_and_writable(tmp_path, monkeypatch):
    directory = tmp_path / "metrics"
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(directory))

    assert observability._prepare_multiprocess_directory() == directory
    assert directory.is_dir()


def test_multiprocess_directory_rejects_a_file(tmp_path, monkeypatch):
    configured_file = tmp_path / "not-a-directory"
    configured_file.write_text("occupied")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(configured_file))

    with pytest.raises(RuntimeError, match="writable directory"):
        observability._prepare_multiprocess_directory()


def test_multiprocess_counters_aggregate_across_workers(tmp_path):
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    increment = (
        "from app.observability import HTTP_REQUESTS;"
        "import sys;"
        "HTTP_REQUESTS.labels(method='GET', route='/probe', "
        "status_code='200').inc(float(sys.argv[1]))"
    )

    for value in ("2", "3"):
        subprocess.run(
            [sys.executable, "-c", increment, value],
            check=True,
            cwd=Path(__file__).parents[1],
            env=environment,
        )

    collect = (
        "from prometheus_client import generate_latest;"
        "from app.observability import _metrics_registry;"
        "print(generate_latest(_metrics_registry()).decode())"
    )
    result = subprocess.run(
        [sys.executable, "-c", collect],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[1],
        env=environment,
    )

    assert 'route="/probe",status_code="200"} 5.0' in result.stdout
