from __future__ import annotations

import pytest

from errors import AppError
from models import UserRecord
from storage.file_repo import FileUserRepository


@pytest.fixture
async def repo(users_file):
    repository = FileUserRepository(file_path=users_file)
    await repository.startup()
    return repository


async def test_list_all_returns_seed(repo):
    users = await repo.list_all()
    emails = [u.email for u in users]
    assert emails == ["noah.thompson206@example.com", "emma.taylor@example.com"]


async def test_search_by_first_name_is_case_insensitive(repo):
    matches = await repo.search_by_first_name("noah")
    assert len(matches) == 1
    assert matches[0].email == "noah.thompson206@example.com"


async def test_search_by_first_name_returns_empty_when_no_match(repo):
    assert await repo.search_by_first_name("nonexistent") == []


async def test_create_appends_record(repo):
    new_user = UserRecord(
        email="ava.brown@example.com",
        first_name="Ava",
        last_name="Brown",
    )
    created = await repo.create(new_user)
    assert created.email == "ava.brown@example.com"

    users = await repo.list_all()
    assert [u.email for u in users][-1] == "ava.brown@example.com"


async def test_create_rejects_duplicate_email(repo):
    duplicate = UserRecord(email="emma.taylor@example.com", first_name="Other")
    with pytest.raises(AppError) as exc_info:
        await repo.create(duplicate)
    assert exc_info.value.status_code == 400


async def test_delete_removes_record(repo):
    deleted = await repo.delete_by_email("emma.taylor@example.com")
    assert deleted.email == "emma.taylor@example.com"
    remaining = await repo.list_all()
    assert [u.email for u in remaining] == ["noah.thompson206@example.com"]


async def test_delete_missing_email_raises_404(repo):
    with pytest.raises(AppError) as exc_info:
        await repo.delete_by_email("ghost@example.com")
    assert exc_info.value.status_code == 404


async def test_update_replaces_record(repo):
    replacement = UserRecord(
        email="noah.updated@example.com",
        first_name="Noah",
        last_name="Thompson",
        phone="+1-000-000-0000",
    )
    updated = await repo.update_by_email("noah.thompson206@example.com", replacement)
    assert updated.email == "noah.updated@example.com"
    assert updated.phone == "+1-000-000-0000"

    users = await repo.list_all()
    assert "noah.thompson206@example.com" not in [u.email for u in users]
    assert "noah.updated@example.com" in [u.email for u in users]


async def test_update_rejects_email_collision(repo):
    replacement = UserRecord(email="emma.taylor@example.com", first_name="Noah")
    with pytest.raises(AppError) as exc_info:
        await repo.update_by_email("noah.thompson206@example.com", replacement)
    assert exc_info.value.status_code == 400
