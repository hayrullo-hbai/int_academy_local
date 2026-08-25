"""Client for the self-hosted ml-runner service (Data Lab code execution)."""

import requests

from app.core.config import settings


def run(code: str, dataset_file: str = "") -> dict:
    resp = requests.post(
        settings.MLRUNNER_URL,
        json={"code": code, "dataset": dataset_file},
        timeout=settings.MLRUNNER_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()
