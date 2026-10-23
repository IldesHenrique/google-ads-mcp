"""Construct each request using the real Google Ads Python SDK, without network calls."""

from google.ads.googleads.client import GoogleAdsClient
from google.oauth2.credentials import Credentials
from proto import Message

from ads_mcp.tools.ads_management import _request


def test_validate_only_request_types_with_real_sdk():
    client = GoogleAdsClient(
        credentials=Credentials(token="fake-no-network-calls"),
        developer_token="fake-no-network-calls",
        use_proto_plus=True,
    )
    customer_id = "1234567890"
    cases = [
        ("create_search_campaign", {
            "campaign_name": "Search campaign paused", "daily_budget_micros": 50000000,
            "contains_eu_political_ads": False,
        }, {}),
        ("update_campaign_budget", {"campaign_id": "123", "new_daily_budget_micros": 75000000}, {
            "budget": {"resource": f"customers/{customer_id}/campaignBudgets/8765"}
        }),
        ("create_ad_group", {
            "campaign_id": "123", "ad_group_name": "Products", "cpc_bid_micros": 1250000
        }, {}),
        ("add_keyword", {
            "ad_group_id": "654", "keyword_text": "products for pools", "match_type": "EXACT"
        }, {}),
        ("create_responsive_search_ad", {
            "ad_group_id": "654", "headlines": ["Swimming Pool", "Pool Products", "Order Online"],
            "descriptions": ["Discover pool supplies and accessories.", "See prices and order securely online."],
            "final_url": "https://example.com/pool",
        }, {}),
    ]
    for action, params, before in cases:
        service_name, method_name, request = _request(
            client, action, customer_id, params, before, validate_only=True
        )
        assert service_name and method_name
        assert request.customer_id == customer_id
        assert request.validate_only is True
        assert request.partial_failure is False
        assert Message.pb(request).SerializeToString(), action
