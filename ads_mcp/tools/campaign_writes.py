"""Google Ads MCP: controlled editing of existing campaigns.

Install at ads_mcp/tools/campaign_writes.py in IldesHenrique/google-ads-mcp.
Dry-run is available immediately; live writes require GOOGLE_ADS_MCP_WRITES_ENABLED=true.
This first module supports PAUSED/ENABLED status changes and renaming only.
"""

from __future__ import annotations

import os
import re
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ads_mcp import utils, write_guard

campaign_writes_mcp = FastMCP("campaign_writes")


def _id(value: str | int, label: str) -> str:
    cleaned = re.sub(r"[-\s]", "", str(value))
    if not cleaned.isdecimal() or len(cleaned) != 10:
        raise ValueError(f"{label} precisa ter exatamente 10 dígitos.")
    return cleaned


def _campaign_id(value: str | int) -> str:
    cleaned = str(value).strip()
    if not cleaned.isdecimal() or int(cleaned) <= 0:
        raise ValueError("campaign_id precisa ser um número positivo.")
    return cleaned


def _get_campaign(
    customer_id: str, campaign_id: str, login_customer_id: str | None
) -> dict[str, str]:
    service = utils.get_googleads_service(
        "GoogleAdsService", login_customer_id=login_customer_id
    )
    query = (
        "SELECT campaign.id, campaign.name, campaign.status "
        f"FROM campaign WHERE campaign.id = {campaign_id} LIMIT 1"
    )
    rows = service.search(customer_id=customer_id, query=query)
    item = next(iter(rows), None)
    if item is None:
        raise ValueError("Campanha não encontrada nesta conta do Google Ads.")
    status = item.campaign.status
    return {
        "customer_id": customer_id,
        "campaign_id": str(item.campaign.id),
        "name": item.campaign.name,
        "status": status.name if hasattr(status, "name") else str(status),
    }


def _changes(new_status: str | None, new_name: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if new_status is not None:
        status = new_status.strip().upper()
        if status not in ("PAUSED", "ENABLED"):
            raise ValueError(
                "new_status deve ser PAUSED ou ENABLED. Exclusão não é permitida."
            )
        result["status"] = status
    if new_name is not None:
        name = new_name.strip()
        if not (1 <= len(name) <= 255):
            raise ValueError("new_name deve ter de 1 a 255 caracteres.")
        result["name"] = name
    if not result:
        raise ValueError("Informe new_status ou new_name.")
    return result


def _operation(
    customer_id: str, campaign_id: str, changes: dict[str, str]
) -> Any:
    client = utils.get_googleads_client()
    operation = client.get_type("CampaignOperation")
    campaign = operation.update
    campaign.resource_name = f"customers/{customer_id}/campaigns/{campaign_id}"
    if "status" in changes:
        campaign.status = getattr(
            client.enums.CampaignStatusEnum, changes["status"]
        )
    if "name" in changes:
        campaign.name = changes["name"]
    operation.update_mask.paths.extend(list(changes))
    return operation


def _mutate_campaign(
    customer_id: str,
    login_customer_id: str | None,
    operation: Any,
    *,
    validate_only: bool,
) -> Any:
    """Send execution flags inside MutateCampaignsRequest (SDK v25+)."""
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    request = client.get_type("MutateCampaignsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.partial_failure = False
    request.validate_only = validate_only
    service = utils.get_googleads_service(
        "CampaignService", login_customer_id=login_customer_id
    )
    return service.mutate_campaigns(request=request)


def _validate(
    customer_id: str, login_customer_id: str | None, operation: Any
) -> None:
    _mutate_campaign(
        customer_id, login_customer_id, operation, validate_only=True
    )


@campaign_writes_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def preview_campaign_update(
    customer_id: str,
    campaign_id: str,
    new_status: str | None = None,
    new_name: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Preview and validate a campaign rename or pause/resume with Google Ads API.

    This tool NEVER changes a live campaign. Always call it before proposing edits.
    Provide customer_id of the client account, not the manager's id.
    """
    customer_id = _id(customer_id, "customer_id")
    campaign_id = _campaign_id(campaign_id)
    if login_customer_id is not None:
        login_customer_id = _id(login_customer_id, "login_customer_id")
    requested = _changes(new_status, new_name)
    before = _get_campaign(customer_id, campaign_id, login_customer_id)
    proposed = {
        field: value
        for field, value in requested.items()
        if before.get(field) != value
    }
    if not proposed:
        return {"validated": True, "no_change": True, "before": before}
    operation = _operation(customer_id, campaign_id, proposed)
    _validate(customer_id, login_customer_id, operation)
    return {
        "validated": True,
        "executed": False,
        "before": before,
        "proposed": proposed,
        "confirmation_text": f"CONFIRMO ALTERAR {customer_id}/{campaign_id}",
        "message": "Google Ads validate_only passou. Nenhuma alteração foi executada.",
    }


@campaign_writes_mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False
    )
)
def apply_campaign_update(
    customer_id: str,
    campaign_id: str,
    expected_name: str,
    expected_status: str,
    confirmation_text: str,
    new_status: str | None = None,
    new_name: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    """Apply a previously previewed campaign change ONLY after explicit user approval.

    This modifies the real Google Ads account. Never call without a fresh preview,
    a user's approval in the conversation, and the exact confirmation phrase.
    Protected by GOOGLE_ADS_MCP_WRITES_ENABLED=true environment flag.
    """
    if (
        os.environ.get("GOOGLE_ADS_MCP_WRITES_ENABLED", "false").lower()
        != "true"
    ):
        raise PermissionError(
            "Escrita desabilitada no servidor (GOOGLE_ADS_MCP_WRITES_ENABLED)."
        )
    customer_id = _id(customer_id, "customer_id")
    campaign_id = _campaign_id(campaign_id)
    if login_customer_id is not None:
        login_customer_id = _id(login_customer_id, "login_customer_id")
    if confirmation_text != f"CONFIRMO ALTERAR {customer_id}/{campaign_id}":
        raise PermissionError(
            "Confirmação explícita da conta e campanha é obrigatória."
        )
    write_guard.assert_write_permitted(customer_id, login_customer_id)
    before = _get_campaign(customer_id, campaign_id, login_customer_id)
    if before["name"] != expected_name or before["status"] != expected_status:
        raise ValueError(
            "A campanha mudou desde a prévia. Consulte novamente antes de aplicar."
        )
    requested = _changes(new_status, new_name)
    proposed = {
        field: value
        for field, value in requested.items()
        if before.get(field) != value
    }
    if not proposed:
        return {"executed": False, "no_change": True, "before": before}
    operation = _operation(customer_id, campaign_id, proposed)
    _validate(customer_id, login_customer_id, operation)
    result = _mutate_campaign(
        customer_id, login_customer_id, operation, validate_only=False
    )
    after = _get_campaign(customer_id, campaign_id, login_customer_id)
    return {
        "executed": True,
        "customer_id": customer_id,
        "campaign_id": campaign_id,
        "resource_name": result.results[0].resource_name,
        "before": before,
        "after": after,
        "requested": proposed,
        "verified": all(
            after.get(key) == value for key, value in proposed.items()
        ),
    }
