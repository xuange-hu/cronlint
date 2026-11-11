"""Fixtures for the cronlint suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from helpers import app_from, report_from  # noqa: E402  (path set above)


@pytest.fixture
def clean_cron():
    """A crontab with nothing to report: absolute path, logged, quoted."""
    return report_from(
        "0 3 * * * /usr/local/bin/nightly >> /var/log/nightly.log 2>&1\n",
        source="crontab",
    )


@pytest.fixture
def bad_cron():
    """A crontab that trips several rules at once."""
    return report_from(
        "PATH=/tmp:/usr/bin\n"
        "* * * * * /tmp/refresh.sh\n"
        "0 3 * * * /opt/backup/nightly.sh --password=hunter2\n",
        source="/etc/cron.d/app",
    )


@pytest.fixture
def empty_cron():
    return report_from("# nothing here\nSHELL=/bin/sh\n", source="empty.cron")


@pytest.fixture
def sample_app():
    return app_from("0 12 * * mon /opt/jobs/weekly-report\n", source="crontab")
