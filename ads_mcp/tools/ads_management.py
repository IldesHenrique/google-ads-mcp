"""Google Ads MCP controlled Search operations (create / budgets / ad groups / keywords / RSA).

Preview sends requests with validate_only=True and stores a short-lived, unguessable
one-time plan in the running process. Execution is server-disabled by default.
These tools are for Search campaigns; other campaign types are rejected.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlparse

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ads_mcp import utils

ads_management_mcp = FastMCP("ads_management")

# Deliberately non-persistent and single-use: a restart invalidates approvals.
_PLANS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_PLAN_TTL_SECONDS = 600
_MAX_PLANS = 100


def _customer_id(value: str | int, label: str = "customer_id") -> str:
    normalized = re.sub(r"[-\s]", "", str(value))
    if not (len(normalized) == 10 and normalized.isdecimal()):
        raise ValueError(f"{label} deve conter exatamente 10 dígitos.")
    return normalized


def _resource_id(value: str | int, label: str) -> str:
    normalized = str(value).strip()
    if not normalized.isdecimal() or int(normalized) < 1:
        raise ValueError(f"{label} deve ser um inteiro positivo.")
    return normalized


def _label(value: str, field: str, *, max_chars: int = 200) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} deve ser texto.")
    normalized = value.strip()
    if not normalized or len(normalized) > max_chars or "\n" in normalized or "\r" in normalized:
        raise ValueError(f"{field} deve ter de 1 a {max_chars} caracteres, sem quebras de linha.")
    return normalized


def _money(value: int, label: str, *, minimum: int = 1_000_000, maximum: int = 10_000_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} deve ser um inteiro entre {minimum} e {maximum} micros.")
    return value


def _gaql(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _query(customer_id: str, manager_id: str | None, query: str) -> list[Any]:
    service = utils.get_googleads_service("GoogleAdsService", login_customer_id=manager_id)
    return list(service.search(customer_id=customer_id, query=query))


def _one(customer_id: str, manager_id: str | None, query: str, description: str) -> Any:
    rows = _query(customer_id, manager_id, query)
    if len(rows) != 1:
        raise ValueError(f"{description}: encontrado(s) {len(rows)} resultado(s), esperado 1.")
    return rows[0]


def _campaign(customer_id: str, manager_id: str | None, campaign_id: str) -> dict[str, Any]:
    row = _one(customer_id, manager_id,
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
        f"campaign.campaign_budget FROM campaign WHERE campaign.id = {campaign_id} LIMIT 1",
        "Campanha")
    campaign = row.campaign
    result = {
        "id": str(campaign.id), "name": campaign.name,
        "status": campaign.status.name, "channel": campaign.advertising_channel_type.name,
        "budget": campaign.campaign_budget,
    }
    if result["status"] == "REMOVED":
        raise ValueError("Campanha removida não pode ser modificada.")
    return result


def _search_campaign(customer_id: str, manager_id: str | None, campaign_id: str) -> dict[str, Any]:
    result = _campaign(customer_id, manager_id, campaign_id)
    if result["channel"] != "SEARCH":
        raise ValueError("Esta função é exclusiva para campanhas de Pesquisa (SEARCH).")
    return result


def _budget(customer_id: str, manager_id: str | None, budget_resource: str) -> dict[str, Any]:
    match = re.fullmatch(rf"customers/{customer_id}/campaignBudgets/([0-9]+)", budget_resource)
    if not match:
        raise ValueError("Recurso de orçamento inválido para esta conta.")
    budget_id = match.group(1)
    row = _one(customer_id, manager_id,
        "SELECT campaign_budget.id, campaign_budget.resource_name, campaign_budget.amount_micros, "
        "campaign_budget.explicitly_shared, campaign_budget.reference_count, "
        f"campaign_budget.period, campaign_budget.status FROM campaign_budget WHERE campaign_budget.id = {budget_id} LIMIT 1",
        "Orçamento")
    item = row.campaign_budget
    return {
        "resource": item.resource_name, "amount_micros": int(item.amount_micros),
        "shared": bool(item.explicitly_shared), "reference_count": int(item.reference_count),
        "period": item.period.name, "status": item.status.name,
    }


def _ad_group(customer_id: str, manager_id: str | None, ad_group_id: str) -> dict[str, Any]:
    row = _one(customer_id, manager_id,
        "SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.type, ad_group.campaign "
        f"FROM ad_group WHERE ad_group.id = {ad_group_id} LIMIT 1",
        "Grupo de anúncios")
    group = row.ad_group
    result = {
        "id": str(group.id), "name": group.name,
        "status": group.status.name, "type": group.type.name,
        "campaign": group.campaign,
    }
    if result["status"] == "REMOVED" or result["type"] != "SEARCH_STANDARD":
        raise ValueError("O grupo precisa estar ativo/pausado e ser SEARCH_STANDARD.")
    match = re.fullmatch(rf"customers/{customer_id}/campaigns/([0-9]+)", result["campaign"])
    if not match:
        raise ValueError("Grupo pertence a outra conta.")
    _search_campaign(customer_id, manager_id, match.group(1))
    return result


def _currency(customer_id: str, manager_id: str | None) -> str:
    return _one(customer_id, manager_id,
        "SELECT customer.currency_code FROM customer LIMIT 1", "Moeda da conta").customer.currency_code


def _campaign_name_available(customer_id: str, manager_id: str | None, name: str) -> None:
    rows = _query(customer_id, manager_id,
        f"SELECT campaign.id FROM campaign WHERE campaign.name = '{_gaql(name)}' LIMIT 1")
    if rows:
        raise ValueError("Já existe uma campanha com este nome.")


def _ad_group_name_available(customer_id: str, manager_id: str | None, campaign_id: str, name: str) -> None:
    rows = _query(customer_id, manager_id,
        "SELECT ad_group.id FROM ad_group "
        f"WHERE campaign.id = {campaign_id} AND ad_group.name = '{_gaql(name)}' LIMIT 1")
    if rows:
        raise ValueError("Já existe um grupo de anúncios com esse nome nesta campanha.")


def _keyword_available(customer_id: str, manager_id: str | None, ad_group_id: str, text: str, match: str) -> None:
    rows = _query(customer_id, manager_id,
        "SELECT ad_group_criterion.criterion_id FROM ad_group_criterion "
        f"WHERE ad_group.id = {ad_group_id} "
        f"AND ad_group_criterion.keyword.text = '{_gaql(text)}' "
        f"AND ad_group_criterion.keyword.match_type = {match} "
        "AND ad_group_criterion.status != REMOVED LIMIT 1")
    if rows:
        raise ValueError("A palavra-chave já existe neste grupo.")


def _normalized_params(action: str, params: dict[str, Any]) -> dict[str, Any]:
    p = dict(params)
    if action == "create_search_campaign":
        p["campaign_name"] = _label(p["campaign_name"], "campaign_name")
        p["daily_budget_micros"] = _money(p["daily_budget_micros"], "daily_budget_micros")
        if type(p["contains_eu_political_ads"]) is not bool:
            raise ValueError("A declaração de publicidade política na UE deve ser true/false.")
    elif action == "update_campaign_budget":
        p["campaign_id"] = _resource_id(p["campaign_id"], "campaign_id")
        p["new_daily_budget_micros"] = _money(p["new_daily_budget_micros"], "new_daily_budget_micros")
    elif action == "create_ad_group":
        p["campaign_id"] = _resource_id(p["campaign_id"], "campaign_id")
        p["ad_group_name"] = _label(p["ad_group_name"], "ad_group_name")
        p["cpc_bid_micros"] = _money(p["cpc_bid_micros"], "cpc_bid_micros", minimum=10_000, maximum=1_000_000_000)
    elif action == "add_keyword":
        p["ad_group_id"] = _resource_id(p["ad_group_id"], "ad_group_id")
        p["keyword_text"] = _label(p["keyword_text"], "keyword_text", max_chars=80)
        p["match_type"] = _label(p["match_type"], "match_type").upper()
        if p["match_type"] not in {"EXACT", "PHRASE", "BROAD"}:
            raise ValueError("match_type deve ser EXACT, PHRASE ou BROAD.")
    elif action == "create_responsive_search_ad":
        p["ad_group_id"] = _resource_id(p["ad_group_id"], "ad_group_id")
        headlines, descriptions = p["headlines"], p["descriptions"]
        if not isinstance(headlines, list) or not 3 <= len(headlines) <= 15:
            raise ValueError("São necessários de 3 a 15 títulos.")
        if not isinstance(descriptions, list) or not 2 <= len(descriptions) <= 4:
            raise ValueError("São necessárias de 2 a 4 descrições.")
        p["headlines"] = [_label(v, "headline", max_chars=30) for v in headlines]
        p["descriptions"] = [_label(v, "description", max_chars=90) for v in descriptions]
        if len(set(p["headlines"])) != len(p["headlines"]):
            raise ValueError("Os títulos devem ser únicos.")
        if len(set(p["descriptions"])) != len(p["descriptions"]):
            raise ValueError("As descrições devem ser únicas.")
        url = _label(p["final_url"], "final_url", max_chars=2048)
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("final_url precisa ser HTTPS válido, sem credenciais ou fragmentos.")
        p["final_url"] = url
    else:
        raise ValueError("Operação não suportada.")
    return p


def _snapshot(action: str, customer_id: str, manager_id: str | None, p: dict[str, Any]) -> dict[str, Any]:
    if action == "create_search_campaign":
        _campaign_name_available(customer_id, manager_id, p["campaign_name"])
        return {"currency": _currency(customer_id, manager_id), "name_is_available": True}
    if action == "update_campaign_budget":
        campaign = _search_campaign(customer_id, manager_id, p["campaign_id"])
        budget = _budget(customer_id, manager_id, campaign["budget"])
        if budget["shared"] or budget["reference_count"] != 1:
            raise ValueError("Orçamento compartilhado. Alteração bloqueada para proteger outras campanhas.")
        if budget["period"] != "DAILY" or budget["status"] != "ENABLED":
            raise ValueError("Somente orçamentos diários habilitados podem ser alterados.")
        if budget["amount_micros"] == p["new_daily_budget_micros"]:
            raise ValueError("O novo orçamento é igual ao orçamento atual.")
        return {"campaign": campaign, "budget": budget, "currency": _currency(customer_id, manager_id)}
    if action == "create_ad_group":
        campaign = _search_campaign(customer_id, manager_id, p["campaign_id"])
        _ad_group_name_available(customer_id, manager_id, p["campaign_id"], p["ad_group_name"])
        return {"campaign": campaign, "name_is_available": True}
    if action == "add_keyword":
        group = _ad_group(customer_id, manager_id, p["ad_group_id"])
        _keyword_available(customer_id, manager_id, p["ad_group_id"], p["keyword_text"], p["match_type"])
        return {"ad_group": group, "keyword_is_available": True}
    if action == "create_responsive_search_ad":
        return {"ad_group": _ad_group(customer_id, manager_id, p["ad_group_id"])}
    raise ValueError("Operação não suportada.")


def _request(client: Any, action: str, customer_id: str, p: dict[str, Any], before: dict[str, Any], validate_only: bool):
    if action == "create_search_campaign":
        req = client.get_type("MutateGoogleAdsRequest")
        req.customer_id = customer_id
        req.validate_only = validate_only
        req.partial_failure = False
        budget_op = client.get_type("MutateOperation")
        budget = budget_op.campaign_budget_operation.create
        budget_resource = f"customers/{customer_id}/campaignBudgets/-1"
        budget.resource_name = budget_resource
        budget.name = f"{p['campaign_name']} | Orçamento"
        budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget.amount_micros = p["daily_budget_micros"]
        budget.explicitly_shared = False
        req.mutate_operations.append(budget_op)
        campaign_op = client.get_type("MutateOperation")
        campaign = campaign_op.campaign_operation.create
        campaign.resource_name = f"customers/{customer_id}/campaigns/-2"
        campaign.name = p["campaign_name"]
        campaign.campaign_budget = budget_resource
        campaign.status = client.enums.CampaignStatusEnum.PAUSED
        campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
        campaign.contains_eu_political_advertising = (
            client.enums.EuPoliticalAdvertisingStatusEnum.CONTAINS_EU_POLITICAL_ADVERTISING
            if p["contains_eu_political_ads"]
            else client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        )
        campaign.manual_cpc = client.get_type("ManualCpc")
        campaign.network_settings.target_google_search = True
        campaign.network_settings.target_search_network = False
        campaign.network_settings.target_content_network = False
        campaign.network_settings.target_partner_search_network = False
        req.mutate_operations.append(campaign_op)
        return "GoogleAdsService", "mutate", req

    if action == "update_campaign_budget":
        op = client.get_type("CampaignBudgetOperation")
        op.update.resource_name = before["budget"]["resource"]
        op.update.amount_micros = p["new_daily_budget_micros"]
        op.update_mask.paths.append("amount_micros")
        request_type, service_type, method, operations = (
            "MutateCampaignBudgetsRequest", "CampaignBudgetService", "mutate_campaign_budgets", [op])
    elif action == "create_ad_group":
        op = client.get_type("AdGroupOperation")
        group = op.create
        group.name = p["ad_group_name"]
        group.campaign = f"customers/{customer_id}/campaigns/{p['campaign_id']}"
        group.status = client.enums.AdGroupStatusEnum.PAUSED
        group.type = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
        group.cpc_bid_micros = p["cpc_bid_micros"]
        request_type, service_type, method, operations = (
            "MutateAdGroupsRequest", "AdGroupService", "mutate_ad_groups", [op])
    elif action == "add_keyword":
        op = client.get_type("AdGroupCriterionOperation")
        criterion = op.create
        criterion.ad_group = f"customers/{customer_id}/adGroups/{p['ad_group_id']}"
        criterion.keyword.text = p["keyword_text"]
        criterion.keyword.match_type = getattr(client.enums.KeywordMatchTypeEnum, p["match_type"])
        criterion.status = client.enums.AdGroupCriterionStatusEnum.PAUSED
        request_type, service_type, method, operations = (
            "MutateAdGroupCriteriaRequest", "AdGroupCriterionService", "mutate_ad_group_criteria", [op])
    elif action == "create_responsive_search_ad":
        op = client.get_type("AdGroupAdOperation")
        group_ad = op.create
        group_ad.ad_group = f"customers/{customer_id}/adGroups/{p['ad_group_id']}"
        group_ad.status = client.enums.AdGroupAdStatusEnum.PAUSED
        ad = group_ad.ad
        ad.final_urls.append(p["final_url"])
        for text in p["headlines"]:
            asset = client.get_type("AdTextAsset")
            asset.text = text
            ad.responsive_search_ad.headlines.append(asset)
        for text in p["descriptions"]:
            asset = client.get_type("AdTextAsset")
            asset.text = text
            ad.responsive_search_ad.descriptions.append(asset)
        request_type, service_type, method, operations = (
            "MutateAdGroupAdsRequest", "AdGroupAdService", "mutate_ad_group_ads", [op])
    else:
        raise ValueError("Operação não suportada.")

    req = client.get_type(request_type)
    req.customer_id = customer_id
    req.validate_only = validate_only
    req.partial_failure = False
    req.operations.extend(operations)
    return service_type, method, req


def _mutate(action: str, customer_id: str, manager_id: str | None, p: dict[str, Any], before: dict[str, Any], *, validate_only: bool):
    client = utils.get_googleads_client(login_customer_id=manager_id)
    service_type, method, request = _request(client, action, customer_id, p, before, validate_only)
    service = utils.get_googleads_service(service_type, login_customer_id=manager_id)
    # No **kwargs flags: the Google Ads Python SDK expects a typed request.
    return getattr(service, method)(request=request)


def _preview(action: str, customer_id: str, params: dict[str, Any], login_customer_id: str | None) -> dict[str, Any]:
    customer_id = _customer_id(customer_id)
    if login_customer_id is not None:
        login_customer_id = _customer_id(login_customer_id, "login_customer_id")
    p = _normalized_params(action, params)
    before = _snapshot(action, customer_id, login_customer_id, p)
    _mutate(action, customer_id, login_customer_id, p, before, validate_only=True)
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _LOCK:
        expired = [key for key, plan in _PLANS.items() if now - plan["created"] > _PLAN_TTL_SECONDS]
        for key in expired:
            _PLANS.pop(key, None)
        while len(_PLANS) >= _MAX_PLANS:
            _PLANS.pop(next(iter(_PLANS)))
        _PLANS[token] = {
            "created": now, "action": action, "customer_id": customer_id,
            "manager_id": login_customer_id, "params": p, "before": before,
        }
    return {
        "validated": True, "executed": False, "action": action,
        "customer_id": customer_id, "current_state": before, "proposed": p,
        "preview_token": token,
        "confirmation_text": f"CONFIRMO {action.upper()} NA CONTA {customer_id}",
        "expires_in_seconds": _PLAN_TTL_SECONDS,
        "notes": "Validação concluída, sem alterações reais. A execução exige aprovação humana explícita e servidor de escrita habilitado.",
    }


@ads_management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def preview_create_search_campaign(
    customer_id: str, campaign_name: str, daily_budget_micros: int,
    contains_eu_political_ads: bool, login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Validate creation of a PAUSED Search campaign and nonshared daily budget.

    Currency is the Google Ads account currency. 50 BRL = 50000000 micros.
    No ads, keywords, or targeting are created with this initial skeleton.
    """
    return _preview("create_search_campaign", customer_id,
        {"campaign_name": campaign_name, "daily_budget_micros": daily_budget_micros,
         "contains_eu_political_ads": contains_eu_political_ads}, login_customer_id)


@ads_management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def preview_update_campaign_budget(
    customer_id: str, campaign_id: str, new_daily_budget_micros: int,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Validate a daily budget adjustment; shared budgets are REJECTED.

    Amount is in micros, e.g., R$ 75/day = 75000000 micros (BRL account).
    """
    return _preview("update_campaign_budget", customer_id,
        {"campaign_id": campaign_id, "new_daily_budget_micros": new_daily_budget_micros}, login_customer_id)


@ads_management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def preview_create_ad_group(
    customer_id: str, campaign_id: str, ad_group_name: str, cpc_bid_micros: int,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Validate creation of a PAUSED Search-standard ad group with manual CPC bid."""
    return _preview("create_ad_group", customer_id,
        {"campaign_id": campaign_id, "ad_group_name": ad_group_name,
         "cpc_bid_micros": cpc_bid_micros}, login_customer_id)


@ads_management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def preview_add_keyword(
    customer_id: str, ad_group_id: str, keyword_text: str, match_type: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Validate adding one PAUSED keyword to an existing Search ad group.

    match_type: EXACT, PHRASE, or BROAD. Negatives are not yet included.
    """
    return _preview("add_keyword", customer_id,
        {"ad_group_id": ad_group_id, "keyword_text": keyword_text,
         "match_type": match_type}, login_customer_id)


@ads_management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def preview_create_responsive_search_ad(
    customer_id: str, ad_group_id: str, headlines: list[str],
    descriptions: list[str], final_url: str, login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Validate creation of a PAUSED responsive Search ad (RSA).

    3-15 unique headlines (max 30 chars each), 2-4 unique descriptions (max 90),
    and an HTTPS final URL. No ad is published during preview.
    """
    return _preview("create_responsive_search_ad", customer_id,
        {"ad_group_id": ad_group_id, "headlines": headlines,
         "descriptions": descriptions, "final_url": final_url}, login_customer_id)


@ads_management_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)
)
def apply_approved_change(preview_token: str, confirmation_text: str) -> dict[str, Any]:
    """EXECUTE one specific, previously validated plan after explicit user approval.

    NEVER call unless the user has individually reviewed and approved the displayed
    preview in the conversation and provided confirmation. This makes REAL CHANGES.
    Environment GOOGLE_ADS_MCP_WRITES_ENABLED=true is additionally required.
    Preview token is single-use, process-local, and expires after 10 minutes.
    """
    if os.environ.get("GOOGLE_ADS_MCP_WRITES_ENABLED", "false").lower() != "true":
        raise PermissionError("Alterações reais desabilitadas no servidor.")
    with _LOCK:
        plan = _PLANS.get(preview_token)
        if not plan:
            raise PermissionError("Prévia inexistente, expirada ou já utilizada.")
        if time.monotonic() - plan["created"] > _PLAN_TTL_SECONDS:
            _PLANS.pop(preview_token, None)
            raise PermissionError("Prévia expirada. Faça uma nova validação.")
        expected = f"CONFIRMO {plan['action'].upper()} NA CONTA {plan['customer_id']}"
        if not secrets.compare_digest(confirmation_text, expected):
            raise PermissionError("Confirmação inválida para esta operação e conta.")
        # Consume BEFORE execution to reject replays, including on request failures.
        _PLANS.pop(preview_token, None)
    current = _snapshot(plan["action"], plan["customer_id"], plan["manager_id"], plan["params"])
    if current != plan["before"]:
        raise ValueError("Os dados mudaram desde a prévia. Valide novamente.")
    # Revalidate fresh state at the Google Ads API immediately before the live request.
    _mutate(plan["action"], plan["customer_id"], plan["manager_id"],
            plan["params"], current, validate_only=True)
    response = _mutate(plan["action"], plan["customer_id"], plan["manager_id"],
                       plan["params"], current, validate_only=False)
    names = _mutate_resource_names(response)
    verification = {"verified": None, "message": "API confirmou a operação; leitura posterior ainda não confirmada."}
    try:
        verification = _verify_readback(plan["action"], plan["customer_id"],
            plan["manager_id"], plan["params"], plan["before"], names)
    except Exception:
        # A successful mutate can be followed by a transient read failure.
        # Always report the confirmed write rather than suggesting an unsafe retry.
        verification = {"verified": None, "message": "Gravação confirmada pela API; verificação posterior indisponível. Consulte antes de repetir."}
    return {
        "executed": True, "action": plan["action"],
        "customer_id": plan["customer_id"], "proposed": plan["params"],
        "results": names, "verification": verification,
        "message": "Operação real executada. Não repita sem conferir os recursos retornados.",
    }


def _verify_readback(action: str, customer_id: str, manager_id: str | None,
                     p: dict[str, Any], before: dict[str, Any], names: list[str]) -> dict[str, Any]:
    if action == "update_campaign_budget":
        state = _budget(customer_id, manager_id, before["budget"]["resource"])
        is_correct = state["amount_micros"] == p["new_daily_budget_micros"]
        return {"verified": is_correct, "budget_after": state}
    if action == "create_search_campaign":
        rows = _query(customer_id, manager_id,
            "SELECT campaign.id, campaign.name, campaign.status FROM campaign "
            f"WHERE campaign.name = '{_gaql(p['campaign_name'])}' LIMIT 2")
        return {"verified": len(rows) == 1 and rows[0].campaign.status.name == "PAUSED",
                "campaign_count": len(rows), "expected_status": "PAUSED"}
    if action == "create_ad_group":
        rows = _query(customer_id, manager_id,
            "SELECT ad_group.id, ad_group.name, ad_group.status FROM ad_group "
            f"WHERE campaign.id = {p['campaign_id']} AND ad_group.name = '{_gaql(p['ad_group_name'])}' LIMIT 2")
        return {"verified": len(rows) == 1 and rows[0].ad_group.status.name == "PAUSED",
                "ad_group_count": len(rows), "expected_status": "PAUSED"}
    if action == "add_keyword":
        rows = _query(customer_id, manager_id,
            "SELECT ad_group_criterion.criterion_id, ad_group_criterion.status FROM ad_group_criterion "
            f"WHERE ad_group.id = {p['ad_group_id']} "
            f"AND ad_group_criterion.keyword.text = '{_gaql(p['keyword_text'])}' "
            f"AND ad_group_criterion.keyword.match_type = {p['match_type']} "
            "AND ad_group_criterion.status != REMOVED LIMIT 2")
        return {"verified": len(rows) == 1 and rows[0].ad_group_criterion.status.name == "PAUSED",
                "keyword_count": len(rows), "expected_status": "PAUSED"}
    if action == "create_responsive_search_ad":
        resources = [name for name in names if "/adGroupAds/" in name]
        if not resources:
            return {"verified": None, "message": "Sem nome de recurso para verificar. Confira no Google Ads."}
        rows = _query(customer_id, manager_id,
            "SELECT ad_group_ad.resource_name, ad_group_ad.status FROM ad_group_ad "
            f"WHERE ad_group_ad.resource_name = '{_gaql(resources[0])}' LIMIT 1")
        return {"verified": len(rows) == 1 and rows[0].ad_group_ad.status.name == "PAUSED",
                "expected_status": "PAUSED"}
    return {"verified": None, "message": "Verificação específica indisponível."}


def _mutate_resource_names(response: Any) -> list[str]:
    names = []
    for item in getattr(response, "results", []):
        if getattr(item, "resource_name", None):
            names.append(item.resource_name)
    for item in getattr(response, "mutate_operation_responses", []):
        for kind in ("campaign_result", "campaign_budget_result"):
            result = getattr(item, kind, None)
            if result is not None and getattr(result, "resource_name", None):
                names.append(result.resource_name)
    return names