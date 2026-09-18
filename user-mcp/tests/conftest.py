from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the project root importable so tests can import top-level modules.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def seed_users() -> list[dict]:
    return [
        {
            "first_name": "Noah",
            "last_name": "Thompson",
            "ssn": "714-83-8341",
            "phone": "+1-461-252-3994",
            "email": "noah.thompson206@example.com",
            "credit_card_number": "2391-5556-0490-6326",
            "ip_address": "34.226.225.173",
        },
        {
            "first_name": "Emma",
            "last_name": "Taylor",
            "ssn": "111-22-3333",
            "phone": "+1-999-111-2222",
            "email": "emma.taylor@example.com",
            "credit_card_number": "4111-1111-1111-1111",
            "ip_address": "10.0.0.2",
        },
    ]


@pytest.fixture
def users_file(tmp_path: Path, seed_users) -> Path:
    file_path = tmp_path / "users_repository.json"
    file_path.write_text(json.dumps(seed_users), encoding="utf-8")
    return file_path
