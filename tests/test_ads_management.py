"""Unit/security tests for MCP write suite. Works without installed Google Ads SDK."""

import importlib.util
import os
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = (
    Path(__file__).resolve().parents[1]
    / "ads_mcp"
    / "tools"
    / "ads_management.py"
)


class Dynamic:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):
        if name in (
            "operations",
            "mutate_operations",
            "paths",
            "final_urls",
            "headlines",
            "descriptions",
            "results",
            "mutate_operation_responses",
        ):
            value = []
        else:
            value = Dynamic()
        self.__dict__[name] = value
        return value


class FakeClient:
    def __init__(self):
        self.enums = Dynamic(
            BudgetDeliveryMethodEnum=SimpleNamespace(STANDARD="STANDARD"),
            CampaignStatusEnum=SimpleNamespace(PAUSED="PAUSED"),
            AdvertisingChannelTypeEnum=SimpleNamespace(SEARCH="SEARCH"),
            EuPoliticalAdvertisingStatusEnum=SimpleNamespace(
                CONTAINS_EU_POLITICAL_ADVERTISING="YES",
                DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING="NO",
            ),
            AdGroupStatusEnum=SimpleNamespace(PAUSED="PAUSED"),
            AdGroupTypeEnum=SimpleNamespace(SEARCH_STANDARD="SEARCH_STANDARD"),
            KeywordMatchTypeEnum=SimpleNamespace(
                EXACT="EXACT", PHRASE="PHRASE", BROAD="BROAD"
            ),
            AdGroupCriterionStatusEnum=SimpleNamespace(PAUSED="PAUSED"),
            AdGroupAdStatusEnum=SimpleNamespace(PAUSED="PAUSED"),
        )

    def get_type(self, _name):
        return Dynamic()


class FakeService:
    def __init__(self):
        self.requests = []

    def mutate(self, *, request):
        self.requests.append(request)
        return Dynamic(results=[Dynamic(resource_name="created")])

    def mutate_campaign_budgets(self, *, request):
        self.requests.append(request)
        return Dynamic(results=[Dynamic(resource_name="budget-edited")])

    def mutate_ad_groups(self, *, request):
        self.requests.append(request)
        return Dynamic(results=[Dynamic(resource_name="adgroup-created")])

    def mutate_ad_group_criteria(self, *, request):
        self.requests.append(request)
        return Dynamic(results=[Dynamic(resource_name="keyword-created")])

    def mutate_ad_group_ads(self, *, request):
        self.requests.append(request)
        return Dynamic(results=[Dynamic(resource_name="ad-created")])


@pytest.fixture
def loaded(monkeypatch):
    # Minimal stand-ins for project imports, not Google Ads SDK functionality.
    class FakeMCP:
        def __init__(self, *a):
            pass

        def tool(self, annotations=None):
            return lambda fn: fn

    fastmcp = types.ModuleType("fastmcp")
    fastmcp.FastMCP = FakeMCP
    mcp = types.ModuleType("mcp")
    mcp.types = types.ModuleType("mcp.types")
    mcp.types.ToolAnnotations = lambda **kwargs: kwargs
    pkg = types.ModuleType("ads_mcp")
    pkg.__path__ = []
    pkg.utils = types.ModuleType("ads_mcp.utils")
    for key, val in {
        "fastmcp": fastmcp,
        "mcp": mcp,
        "mcp.types": mcp.types,
        "ads_mcp": pkg,
        "ads_mcp.utils": pkg.utils,
    }.items():
        monkeypatch.setitem(sys.modules, key, val)
    spec = importlib.util.spec_from_file_location(
        "test_target_ads_management", MODULE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = FakeClient()
    services = {
        k: FakeService()
        for k in (
            "GoogleAdsService",
            "CampaignBudgetService",
            "AdGroupService",
            "AdGroupCriterionService",
            "AdGroupAdService",
        )
    }
    pkg.utils.get_googleads_client = lambda login_customer_id=None: client
    pkg.utils.get_googleads_service = (
        lambda name, login_customer_id=None: services[name]
    )
    return module, client, services


CAMPAIGN = {
    "id": "123",
    "name": "Search existing",
    "status": "PAUSED",
    "channel": "SEARCH",
    "budget": "customers/1234567890/campaignBudgets/8765",
}
BUDGET = {
    "resource": "customers/1234567890/campaignBudgets/8765",
    "amount_micros": 50000000,
    "shared": False,
    "reference_count": 1,
    "period": "DAILY",
    "status": "ENABLED",
}
GROUP = {
    "id": "654",
    "name": "Default",
    "status": "PAUSED",
    "type": "SEARCH_STANDARD",
    "campaign": "customers/1234567890/campaigns/123",
}
ID = "1234567890"


def example_actions():
    return [
        (
            "create_search_campaign",
            {
                "campaign_name": "New campaign",
                "daily_budget_micros": 50000000,
                "contains_eu_political_ads": False,
            },
        ),
        (
            "update_campaign_budget",
            {"campaign_id": "123", "new_daily_budget_micros": 75000000},
        ),
        (
            "create_ad_group",
            {
                "campaign_id": "123",
                "ad_group_name": "New group",
                "cpc_bid_micros": 1500000,
            },
        ),
        (
            "add_keyword",
            {
                "ad_group_id": "654",
                "keyword_text": "piscinas em campo grande",
                "match_type": "EXACT",
            },
        ),
        (
            "create_responsive_search_ad",
            {
                "ad_group_id": "654",
                "headlines": [
                    "Piscinas em MS",
                    "Produtos para Piscinas",
                    "Confira os Produtos",
                ],
                "descriptions": [
                    "Compre produtos para sua piscina com praticidade.",
                    "Confira nossas ofertas e escolha os produtos.",
                ],
                "final_url": "https://example.com/piscinas",
            },
        ),
    ]


def setup_snapshot(monkeypatch, module):
    monkeypatch.setattr(module, "_currency", lambda *a: "BRL")
    monkeypatch.setattr(module, "_campaign_name_available", lambda *a: None)
    monkeypatch.setattr(module, "_ad_group_name_available", lambda *a: None)
    monkeypatch.setattr(module, "_keyword_available", lambda *a: None)
    monkeypatch.setattr(module, "_search_campaign", lambda *a: dict(CAMPAIGN))
    monkeypatch.setattr(module, "_budget", lambda *a: dict(BUDGET))
    monkeypatch.setattr(module, "_ad_group", lambda *a: dict(GROUP))


@pytest.mark.parametrize("action,params", example_actions())
def test_preview_uses_validate_only_and_paused_creations(
    loaded, monkeypatch, action, params
):
    module, client, services = loaded
    setup_snapshot(monkeypatch, module)
    preview = module._preview(action, ID, params, "9292015368")
    assert preview["validated"] and not preview["executed"]
    assert preview["preview_token"]
    requests = [
        req for service in services.values() for req in service.requests
    ]
    assert len(requests) == 1 and requests[0].validate_only is True
    request = requests[0]
    if action == "create_search_campaign":
        assert (
            request.mutate_operations[
                0
            ].campaign_budget_operation.create.explicitly_shared
            is False
        )
        assert (
            request.mutate_operations[1].campaign_operation.create.status
            == "PAUSED"
        )
        assert request.mutate_operations[
            1
        ].campaign_operation.create.campaign_budget.endswith("-1")
    if action == "create_ad_group":
        assert request.operations[0].create.status == "PAUSED"
    if action == "add_keyword":
        assert request.operations[0].create.status == "PAUSED"
    if action == "create_responsive_search_ad":
        assert request.operations[0].create.status == "PAUSED"
        assert (
            len(request.operations[0].create.ad.responsive_search_ad.headlines)
            == 3
        )


@pytest.mark.parametrize("action,params", example_actions())
def test_write_disabled_by_default_for_all_actions(
    loaded, monkeypatch, action, params
):
    module, _, services = loaded
    setup_snapshot(monkeypatch, module)
    preview = module._preview(action, ID, params, None)
    monkeypatch.delenv("GOOGLE_ADS_MCP_WRITES_ENABLED", raising=False)
    with pytest.raises(PermissionError, match="desabilitadas"):
        module.apply_approved_change(
            preview["preview_token"], preview["confirmation_text"]
        )
    assert sum(len(s.requests) for s in services.values()) == 1


def test_one_time_token_and_confirmation(loaded, monkeypatch):
    module, _, services = loaded
    setup_snapshot(monkeypatch, module)
    preview = module._preview(
        "create_search_campaign", ID, example_actions()[0][1], None
    )
    monkeypatch.setenv("GOOGLE_ADS_MCP_WRITES_ENABLED", "true")
    with pytest.raises(PermissionError, match="Confirmação inválida"):
        module.apply_approved_change(
            preview["preview_token"], "CONFIRMO OUTRA COISA"
        )
    assert sum(len(s.requests) for s in services.values()) == 1
    result = module.apply_approved_change(
        preview["preview_token"], preview["confirmation_text"]
    )
    assert result["executed"]
    assert [r.validate_only for r in services["GoogleAdsService"].requests] == [
        True,
        True,
        False,
    ]
    with pytest.raises(PermissionError, match="utilizada"):
        module.apply_approved_change(
            preview["preview_token"], preview["confirmation_text"]
        )


def test_budget_shared_is_rejected(loaded, monkeypatch):
    module, _, services = loaded
    setup_snapshot(monkeypatch, module)
    monkeypatch.setattr(
        module, "_budget", lambda *args: {**BUDGET, "shared": True}
    )
    with pytest.raises(ValueError, match="compartilhado"):
        module._preview(
            "update_campaign_budget",
            ID,
            {"campaign_id": "123", "new_daily_budget_micros": 90000000},
            None,
        )
    assert all(not s.requests for s in services.values())


def test_plan_expires_without_writes(loaded, monkeypatch):
    module, _, services = loaded
    setup_snapshot(monkeypatch, module)
    preview = module._preview(
        "create_search_campaign", ID, example_actions()[0][1], None
    )
    monkeypatch.setenv("GOOGLE_ADS_MCP_WRITES_ENABLED", "true")
    plan = module._PLANS[preview["preview_token"]]
    plan["created"] -= 601
    with pytest.raises(PermissionError, match="expirada"):
        module.apply_approved_change(
            preview["preview_token"], preview["confirmation_text"]
        )
    assert sum(len(s.requests) for s in services.values()) == 1


def test_external_change_invalidates_plan(loaded, monkeypatch):
    module, _, services = loaded
    setup_snapshot(monkeypatch, module)
    preview = module._preview(
        "update_campaign_budget",
        ID,
        {"campaign_id": "123", "new_daily_budget_micros": 60000000},
        None,
    )
    monkeypatch.setenv("GOOGLE_ADS_MCP_WRITES_ENABLED", "true")
    monkeypatch.setattr(
        module, "_budget", lambda *a: {**BUDGET, "amount_micros": 55000000}
    )
    with pytest.raises(ValueError, match="mudaram"):
        module.apply_approved_change(
            preview["preview_token"], preview["confirmation_text"]
        )
    assert sum(len(s.requests) for s in services.values()) == 1


@pytest.mark.parametrize("value", [True, 5.0, 500, -1, 0, 10_000_000_001])
def test_rejects_bad_budget(loaded, value):
    module, _, _ = loaded
    with pytest.raises(ValueError):
        module._normalized_params(
            "create_search_campaign",
            {
                "campaign_name": "Test",
                "daily_budget_micros": value,
                "contains_eu_political_ads": False,
            },
        )


@pytest.mark.parametrize(
    "values",
    [
        (["Duplicate", "Duplicate", "Third"], ["desc 1", "desc 2"]),
        (["One", "Two"], ["desc 1", "desc 2"]),
        (["A", "B", "C"], ["Same", "Same"]),
        (["A", "B", "C"], ["One", "Two"]),
    ],
)
def test_rsa_rejects_bad_copy_or_url(loaded, values):
    module, _, _ = loaded
    titles, descriptions = values
    if titles == ["A", "B", "C"] and descriptions == ["One", "Two"]:
        url = "javascript:alert(1)"
    else:
        url = "https://example.com"
    with pytest.raises(ValueError):
        module._normalized_params(
            "create_responsive_search_ad",
            {
                "ad_group_id": "654",
                "headlines": titles,
                "descriptions": descriptions,
                "final_url": url,
            },
        )


def test_validate_status_method_requires_server_authorization(
    loaded, monkeypatch
):
    module, _, services = loaded
    setup_snapshot(monkeypatch, module)
    preview = module._preview(
        "create_ad_group", ID, example_actions()[2][1], None
    )
    monkeypatch.setenv("GOOGLE_ADS_MCP_WRITES_ENABLED", "TRUE")
    result = module.apply_approved_change(
        preview["preview_token"], preview["confirmation_text"]
    )
    assert result["executed"]
    assert [r.validate_only for r in services["AdGroupService"].requests] == [
        True,
        True,
        False,
    ]
