from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken

import db

logger = logging.getLogger("poller")

SECRETS_ENCRYPTION_KEY = os.getenv("SECRETS_ENCRYPTION_KEY", "").strip()
DEFAULT_CONCURRENCY = max(1, int(os.getenv("COLLECTOR_CONCURRENCY", "10")))
DEFAULT_NETWORK_RETRIES = max(0, int(os.getenv("COLLECTOR_NETWORK_RETRIES", "2")))

_fernet_instance: Fernet | None = None


def _get_fernet_or_none() -> Fernet | None:
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance
    if not SECRETS_ENCRYPTION_KEY:
        return None
    try:
        _fernet_instance = Fernet(SECRETS_ENCRYPTION_KEY.encode("utf-8"))
        return _fernet_instance
    except Exception:
        logger.error("SECRETS_ENCRYPTION_KEY задан некорректно")
        return None


def decrypt_password(ciphertext: str) -> str:
    fernet = _get_fernet_or_none()
    if fernet is None:
        return ciphertext  # если ключ не задан — считаем что хранится открытым текстом
    try:
        return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("Не удалось расшифровать пароль, используем как есть")
        return ciphertext


def encrypt_password(plaintext: str) -> str:
    fernet = _get_fernet_or_none()
    if fernet is None:
        return plaintext
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


# --- Auth ---

async def sign_in(client: httpx.AsyncClient, auth_base_url: str, login: str, password: str) -> tuple[str, int]:
    payload = {"login": login, "passwd": password, "rememberMe": True, "companyId": 1}
    resp = await client.put(f"{auth_base_url}/1.0/Auth/SignInByEmail", json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    return body["access_Token"], int(body.get("expires_in", 100000))


def _token_is_expired(token_state: dict[str, Any]) -> bool:
    age = (dt.datetime.utcnow() - token_state["obtained_at"]).total_seconds()
    return age > max(60, token_state["expires_in"] - 300)


async def _get_or_refresh_token(
    client: httpx.AsyncClient,
    source_id: int,
    source: dict[str, Any],
    token_cache: dict[int, dict[str, Any]],
    source_locks: dict[int, asyncio.Lock],
    force_refresh: bool = False,
) -> str:
    lock = source_locks.setdefault(source_id, asyncio.Lock())
    async with lock:
        state = token_cache.get(source_id)
        if state and not force_refresh and not _token_is_expired(state):
            return str(state["access_token"])
        token, expires_in = await sign_in(
            client, source["auth_base_url"], source["login"], decrypt_password(source["password_enc"])
        )
        token_cache[source_id] = {
            "access_token": token,
            "obtained_at": dt.datetime.utcnow(),
            "expires_in": expires_in,
        }
        return token


# --- Fetch helmets_conditions with pagination ---

async def fetch_radar_counts(
    client: httpx.AsyncClient,
    api_base_url: str,
    access_token: str,
    company_id: int,
) -> tuple[int, int]:
    """Return (total_radars, online_radars) fetching all pages."""
    total = 0
    online = 0
    skip = 0
    take = 1000

    while True:
        resp = await client.get(
            f"{api_base_url}/1.0/Helmets/Conditions",
            headers={"Authorization": access_token},
            params={"CompanyId": company_id, "Skip": skip, "Take": take},
            timeout=60,
        )
        if resp.status_code == 401:
            raise PermissionError("Token expired")
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or []

        for item in data:
            attrs = item.get("attributes") or {}
            if attrs.get("typeId") != "Radar":
                continue
            total += 1
            cond = item.get("condition") or {}
            online_cond = (cond.get("online") or {}).get("condition")
            if online_cond == "Online":
                online += 1

        if len(data) < take:
            break
        skip += take

    return total, online


async def _fetch_source_with_retry(
    client: httpx.AsyncClient,
    source: dict[str, Any],
    token_cache: dict[int, dict[str, Any]],
    source_locks: dict[int, asyncio.Lock],
) -> tuple[int, int]:
    source_id = source["id"]
    retried_401 = False
    network_attempt = 0

    while True:
        try:
            token = await _get_or_refresh_token(client, source_id, source, token_cache, source_locks)
            return await fetch_radar_counts(client, source["api_base_url"], token, source["company_id"])
        except PermissionError:
            if retried_401:
                raise
            retried_401 = True
            await _get_or_refresh_token(client, source_id, source, token_cache, source_locks, force_refresh=True)
            logger.warning("Повтор после ре-логина | source_id=%s", source_id)
            continue
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and network_attempt < DEFAULT_NETWORK_RETRIES:
                network_attempt += 1
                await asyncio.sleep(2 ** network_attempt)
                continue
            raise
        except (httpx.RequestError, asyncio.TimeoutError):
            if network_attempt < DEFAULT_NETWORK_RETRIES:
                network_attempt += 1
                await asyncio.sleep(2 ** network_attempt)
                continue
            raise


# --- Test fetch (single call for source verification) ---

async def test_fetch_config(
    auth_base_url: str, api_base_url: str, login: str, password: str, company_id: int
) -> tuple[int, int]:
    """Test fetch using raw credentials (before saving to DB). Returns (total, online)."""
    source = {
        "id": -1,
        "name": "test",
        "auth_base_url": auth_base_url,
        "api_base_url": api_base_url,
        "login": login,
        "password_enc": encrypt_password(password),
        "company_id": company_id,
        "poll_interval": 60,
    }
    token_cache: dict[int, dict[str, Any]] = {}
    source_locks: dict[int, asyncio.Lock] = {}
    async with httpx.AsyncClient() as client:
        return await _fetch_source_with_retry(client, source, token_cache, source_locks)


async def test_fetch_source(source_id: int) -> tuple[int, int]:
    """Single test fetch to verify source credentials. Returns (total, online)."""
    source = await db.get_source(source_id)
    if not source:
        raise ValueError(f"Источник {source_id} не найден")
    token_cache: dict[int, dict[str, Any]] = {}
    source_locks: dict[int, asyncio.Lock] = {}
    async with httpx.AsyncClient() as client:
        return await _fetch_source_with_retry(client, source, token_cache, source_locks)


# --- Main polling loop ---

async def run_poller(on_snapshot=None) -> None:
    """
    on_snapshot: async callable(source_id, source_name, total, online)
    Called after each successful poll.
    """
    token_cache: dict[int, dict[str, Any]] = {}
    source_locks: dict[int, asyncio.Lock] = {}
    semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)
    last_poll: dict[int, float] = {}  # source_id → last poll timestamp

    logger.info("Поллер запущен")

    async with httpx.AsyncClient() as client:
        while True:
            now = time.time()
            sources = await db.get_all_active_sources()

            async def poll_one(source: dict[str, Any]) -> None:
                source_id = source["id"]
                interval = source["poll_interval"]
                last = last_poll.get(source_id, 0)
                if now - last < interval:
                    return

                async with semaphore:
                    try:
                        total, online = await _fetch_source_with_retry(
                            client, source, token_cache, source_locks
                        )
                        ts = int(time.time())
                        await db.save_snapshot(source_id, ts, total, online)
                        last_poll[source_id] = ts
                        logger.info(
                            "Опрос OK | source=%s (%s) | total=%s online=%s",
                            source_id, source["name"], total, online,
                        )
                        if on_snapshot:
                            await on_snapshot(source_id, source["name"], total, online)
                    except Exception as exc:
                        last_poll[source_id] = time.time()
                        logger.error("Ошибка опроса | source=%s (%s) | %s", source_id, source["name"], exc)

            if sources:
                await asyncio.gather(*(poll_one(s) for s in sources))

            await asyncio.sleep(10)  # проверяем каждые 10 сек, опрашиваем по интервалу
