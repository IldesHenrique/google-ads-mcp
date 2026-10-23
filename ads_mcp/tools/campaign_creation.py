"""Validate a PAUSED Search campaign skeleton with an independent daily budget.

No creation action is exposed in this phase. Google Ads validate_only is ALWAYS True.
The skeleton has no ad group, keywords, ads or targeting; it cannot serve ads.
"""

from __future__ import annotations

import re
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ads_mcp import utils

campaign_creation_mcp = FastMCP("campaign_creation")


def _id(value: str | int, field: str) -> str:
    normalized = re.sub(r"[-\s]", "", str(value))
    if len(normalized) != 10 or not normalized.isdecimal():
        raise ValueError(f"{field} precisa ter 10 dígitos.")
    return normalized


def _inputs(name: str, budget_micros: int, eu_political_ads: bool) -> tuple[str, int]:
    if not isinstance(name, str):
        raise ValueError("campaign_name deve ser texto.")
    name = name.strip()
    if not 1 <= len(name) <= 200 or any(c in name for c in "\r\n"):
        raise ValueError("Nome inválido: use de 1 a 200 caracteres em uma linha.")
    if isinstance(budget_micros, bool) or not isinstance(budget_micros, int):
        raise ValueError("daily_budget_micros deve ser um número inteiro.")
    # Guardrail for an initial pilot: 1 to 10000 units of account currency.
    if not 1_000_000 <= budget_micros <= 10_000_000_000:
        raise ValueError("Orçamento diário permitido: 1 a 10.000 unidades da moeda da conta.")
    if not isinstance(eu_political_ads, bool):
        raise ValueError("contains_eu_political_ads exige declaração explícita true/false.")
    return name, budget_micros


def _currency(customer_id: str, login_customer_id: str | None) -> str:
    service = utils.get_googleads_service("GoogleAdsService", login_customer_id=login_customer_id)
    result = service.search(
        customer_id=customer_id,
        query="SELECT customer.currency_code FROM customer LIMIT 1",
    )
    row = next(iter(result), None)
    if row is None:
        raise ValueError("Conta não encontrada ou sem acesso.")
    return row.customer.currency_code


def _ensure_name_available(customer_id: str, login_customer_id: str | None, name: str) -> None:
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    service = utils.get_googleads_service("GoogleAdsService", login_customer_id=login_customer_id)
    result = service.search(
        customer_id=customer_id,
        query=f"SELECT campaign.id FROM campaign WHERE campaign.name = '{escaped}' LIMIT 1",
    )
    if next(iter(result), None) is not None:
        raise ValueError("Já existe uma campanha com este nome nesta conta.")


def _request(client: Any, customer_id: str, name: str, budget_micros: int, eu_political_ads: bool) -> Any:
    request = client.get_type("MutateGoogleAdsRequest")
    request.customer_id = customer_id
    request.partial_failure = False
    request.validate_only = True

    budget_operation = client.get_type("MutateOperation")
    budget = budget_operation.campaign_budget_operation.create
    budget_resource = f"customers/{customer_id}/campaignBudgets/-1"
    budget.resource_name = budget_resource
    budget.name = f"{name} | Budget"
    budget.amount_micros = budget_micros
    budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
    budget.explicitly_shared = False
    request.mutate_operations.append(budget_operation)

    campaign_operation = client.get_type("MutateOperation")
    campaign = campaign_operation.campaign_operation.create
    campaign.resource_name = f"customers/{customer_id}/campaigns/-2"
    campaign.name = name
    campaign.campaign_budget = budget_resource
    campaign.status = client.enums.CampaignStatusEnum.PAUSED
    campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
    campaign.contains_eu_political_advertising = (
        client.enums.EuPoliticalAdvertisingStatusEnum.CONTAINS_EU_POLITICAL_ADVERTISING
        if eu_political_ads
        else client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    client.copy_from(campaign.manual_cpc, client.get_type("ManualCpc"))
    campaign.network_settings.target_google_search = True
    campaign.network_settings.target_search_network = False
    campaign.network_settings.target_content_network = False
    request.mutate_operations.append(campaign_operation)
    return request


@campaign_creation_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def preview_search_campaign_setup(
    customer_id: str,
    campaign_name: str,
    daily_budget_micros: int,
    contains_eu_political_ads: bool,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Validate a new PAUSED Search campaign plus dedicated DAILY budget without creating it.

    This ONLY validates a skeleton, not a complete serving campaign.
    Requires explicit political-ads declaration and an amount in MICROS
    (e.g. 50 BRL per day = 50000000 micros). Currency is read from the account.
    No ads, keywords, ad groups, or locations are configured here.
    The method is hard-coded to validate_only=True; no live mutate exists.
    """
    customer_id = _id(customer_id, "customer_id")
    if login_customer_id is not None:
        login_customer_id = _id(login_customer_id, "login_customer_id")
    name, budget = _inputs(campaign_name, daily_budget_micros, contains_eu_political_ads)
    currency = _currency(customer_id, login_customer_id)
    _ensure_name_available(customer_id, login_customer_id, name)
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    request = _request(client, customer_id, name, budget, contains_eu_political_ads)
    if request.validate_only is not True:
        raise RuntimeError("Validação bloqueada: validate_only desativado.")
    service = utils.get_googleads_service("GoogleAdsService", login_customer_id=login_customer_id)
    service.mutate(request=request)
    return {
        "validated": True,
        "executed": False,
        "customer_id": customer_id,
        "campaign_name": name,
        "campaign_status": "PAUSED",
        "channel": "SEARCH",
        "bidding": "MANUAL_CPC",
        "daily_budget_micros": budget,
        "currency_code": currency,
        "contains_eu_political_ads": contains_eu_political_ads,
        "note": "Somente estrutura de campanha e orçamento. Sem grupos, anúncios, palavras-chave ou segmentação.",
    }
