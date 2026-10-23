"""Fail-closed authorization for real Google Ads MCP writes."""

from __future__ import annotations
import hashlib
import os
import re
from ads_mcp import utils

def _allowed_customer_ids() -> set[str]:
    raw = os.environ.get("GOOGLE_ADS_MCP_ALLOWED_WRITE_CUSTOMER_IDS", "")
    if not raw.strip():
        return set()
    values = [value.strip() for value in raw.split(",")]
    if any(not re.fullmatch(r"\d{10}", value) for value in values):
        raise PermissionError("Lista de contas autorizadas inválida: use IDs de 10 dígitos.")
    return set(values)

def _is_test_account(customer_id: str, login_customer_id: str | None) -> bool:
    service = utils.get_googleads_service("GoogleAdsService", login_customer_id=login_customer_id)
    rows = service.search(customer_id=customer_id, query="SELECT customer.test_account FROM customer LIMIT 1")
    result = next(iter(rows), None)
    if result is None:
        raise PermissionError("Não foi possível confirmar o tipo da conta.")
    return result.customer.test_account is True

def assert_write_permitted(customer_id: str, login_customer_id: str | None = None) -> None:
    """Requires server switch, exact customer allowlist, and test-account by default."""
    if os.environ.get("GOOGLE_ADS_MCP_WRITES_ENABLED", "false").strip().lower() != "true":
        raise PermissionError("Alterações reais desabilitadas no servidor.")
    if not re.fullmatch(r"\d{10}", str(customer_id)):
        raise PermissionError("ID de conta inválido.")
    if customer_id not in _allowed_customer_ids():
        raise PermissionError("Conta fora da lista de escrita permitida (GOOGLE_ADS_MCP_ALLOWED_WRITE_CUSTOMER_IDS).")
    if os.environ.get("GOOGLE_ADS_MCP_TEST_ACCOUNTS_ONLY", "true").strip().lower() != "false":
        if not _is_test_account(customer_id, login_customer_id):
            raise PermissionError("Escrita permitida somente em contas de teste (GOOGLE_ADS_MCP_TEST_ACCOUNTS_ONLY).")

def current_principal_fingerprint() -> str:
    """Bind approval to the authenticated bearer, never expose its raw value."""
    from fastmcp.server.dependencies import get_access_token
    access_token = get_access_token()
    raw_token = getattr(access_token, "token", None)
    if not isinstance(raw_token, str) or not raw_token:
        raise PermissionError("Token OAuth ausente: operação bloqueada.")
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
