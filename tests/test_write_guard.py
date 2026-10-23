"""Security regression tests for the real write authorization guard."""
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
import pytest

FILE=Path(__file__).resolve().parents[1]/"ads_mcp"/"write_guard.py"

@pytest.fixture
def guard(monkeypatch):
    pkg=types.ModuleType("ads_mcp")
    pkg.__path__=[]
    utils=types.ModuleType("ads_mcp.utils")
    pkg.utils=utils
    monkeypatch.setitem(sys.modules,"ads_mcp",pkg)
    monkeypatch.setitem(sys.modules,"ads_mcp.utils",utils)
    spec=importlib.util.spec_from_file_location("security_guard_test",FILE)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("GOOGLE_ADS_MCP_WRITES_ENABLED","GOOGLE_ADS_MCP_ALLOWED_WRITE_CUSTOMER_IDS","GOOGLE_ADS_MCP_TEST_ACCOUNTS_ONLY"):
        monkeypatch.delenv(name,raising=False)
    utils.get_googleads_service=lambda name,login_customer_id=None: SimpleNamespace(
        search=lambda **kw:[SimpleNamespace(customer=SimpleNamespace(test_account=True))])
    return mod

def test_closed_by_default(guard):
    with pytest.raises(PermissionError,match="desabilitadas"):
        guard.assert_write_permitted("1234567890")

def test_global_switch_not_enough(guard,monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_MCP_WRITES_ENABLED","true")
    with pytest.raises(PermissionError,match="fora da lista"):
        guard.assert_write_permitted("1234567890")

def test_customer_allowlist(guard,monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_MCP_WRITES_ENABLED","true")
    monkeypatch.setenv("GOOGLE_ADS_MCP_ALLOWED_WRITE_CUSTOMER_IDS","1234567890")
    assert guard.assert_write_permitted("1234567890") is None
    with pytest.raises(PermissionError,match="fora da lista"):
        guard.assert_write_permitted("0987654321")

def test_invalid_allowlist(guard,monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_MCP_WRITES_ENABLED","true")
    monkeypatch.setenv("GOOGLE_ADS_MCP_ALLOWED_WRITE_CUSTOMER_IDS","1234567890,oops")
    with pytest.raises(PermissionError,match="inválida"):
        guard.assert_write_permitted("1234567890")

def test_blocks_production(guard,monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_MCP_WRITES_ENABLED","true")
    monkeypatch.setenv("GOOGLE_ADS_MCP_ALLOWED_WRITE_CUSTOMER_IDS","1234567890")
    guard.utils.get_googleads_service=lambda *a,**k: SimpleNamespace(
        search=lambda **kw:[SimpleNamespace(customer=SimpleNamespace(test_account=False))])
    with pytest.raises(PermissionError,match="contas de teste"):
        guard.assert_write_permitted("1234567890")

def test_explicit_production_override(guard,monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_MCP_WRITES_ENABLED","true")
    monkeypatch.setenv("GOOGLE_ADS_MCP_ALLOWED_WRITE_CUSTOMER_IDS","1234567890")
    monkeypatch.setenv("GOOGLE_ADS_MCP_TEST_ACCOUNTS_ONLY","false")
    assert guard.assert_write_permitted("1234567890") is None

def test_principal_fingerprint(guard,monkeypatch):
    fm=types.ModuleType("fastmcp")
    fm.server=types.ModuleType("fastmcp.server")
    fm.server.dependencies=types.ModuleType("fastmcp.server.dependencies")
    fm.server.dependencies.get_access_token=lambda:SimpleNamespace(token="bearer-A")
    for value in (fm,fm.server,fm.server.dependencies):
        monkeypatch.setitem(sys.modules,value.__name__,value)
    original=guard.current_principal_fingerprint()
    assert len(original)==64 and original!="bearer-A"
    fm.server.dependencies.get_access_token=lambda:SimpleNamespace(token="bearer-B")
    assert guard.current_principal_fingerprint()!=original
    fm.server.dependencies.get_access_token=lambda:None
    with pytest.raises(PermissionError,match="OAuth ausente"):
        guard.current_principal_fingerprint()
