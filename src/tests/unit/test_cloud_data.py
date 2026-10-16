from __future__ import annotations

from unittest.mock import Mock, call, patch

import httpx
import pytest
from app.database import SqlClaimsStore, connection_string, connector, payload
from app.knowledge import SearchPolicyKnowledge
from app.search_rest import KnowledgeError, SearchRestClient
from app.service import DemoService, ResourceNotFound
from app.settings import Settings


def test_runtime_sql_auth_is_explicit_and_encrypted() -> None:
    client_id = "00000000-0000-0000-0000-000000000001"
    value = connection_string("demo.database.windows.net", "claims", client_id=client_id)
    assert "Authentication=ActiveDirectoryMSI" in value
    assert f"UID={client_id}" in value
    assert "Encrypt=yes;TrustServerCertificate=no" in value
    assert "Connection Timeout" not in value
    assert "ActiveDirectoryDefault" in connection_string(
        "demo.database.windows.net", "claims", local_admin=True
    )
    for server, database in (
        ("attacker.example", "claims"),
        ("demo.database.windows.net;TrustServerCertificate=yes", "claims"),
        ("demo.database.windows.net", "claims;UID=another"),
    ):
        with pytest.raises(ValueError):
            connection_string(server, database, client_id=client_id)
    with pytest.raises(ValueError):
        connection_string("demo.database.windows.net", "claims")
    with pytest.raises(ValueError):
        connection_string("demo.database.windows.net", "claims", client_id="invalid")


def test_sql_login_and_query_timeouts_are_bounded() -> None:
    with patch("mssql_python.connect") as connect:
        connection = connector("validated-connection-string")()
    connect.assert_called_once_with("validated-connection-string", timeout=30)
    assert connection.timeout == 30


@pytest.mark.parametrize("content", [b"not JSON", b"\xff", b"[]"])
def test_malformed_search_responses_do_not_escape_as_success(content) -> None:
    rest = SearchRestClient("https://demo.search.windows.net", Mock())
    rest.http.close()
    rest.http = httpx.Client(
        base_url="https://demo.search.windows.net",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content)),
    )
    try:
        with pytest.raises(KnowledgeError):
            rest.request("POST", "/knowledgebases/synthetic-policy-knowledge/retrieve")
        with pytest.raises(ValueError):
            rest.request("GET", "//attacker.example")
    finally:
        rest.close()


def test_fixed_sql_queries_bind_input_and_close_connections() -> None:
    connection = Mock()
    cursor = connection.cursor.return_value
    claim = DemoService().get_claim("clm-demo-001")
    cursor.fetchall.return_value = [(payload(claim),)]
    store = SqlClaimsStore(lambda: connection)
    malicious = "'; DROP TABLE claims.Claim;--"
    assert store.find_claim(malicious) == claim
    sql, params = cursor.execute.call_args.args
    assert malicious not in sql
    assert params == (malicious,)
    cursor.close.assert_called_once()
    connection.close.assert_called_once()


def test_sql_review_write_is_bounded_and_committed() -> None:
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.fetchall.return_value = [("note-id",)]
    store = SqlClaimsStore(lambda: connection)
    assert store.add_review_note("clm-demo-001", "Synthetic review only.") == "note-id"
    assert cursor.execute.call_args.args == (
        "EXEC claims.AddReviewNote @claim_id = ?, @note = ?",
        ("clm-demo-001", "Synthetic review only."),
    )
    connection.commit.assert_called_once()
    for note in (" ", "", "a" * 2001):
        with pytest.raises(ValueError):
            store.add_review_note("clm-demo-001", note)


def test_cloud_service_does_not_load_or_fall_back_to_fixtures() -> None:
    store = Mock()
    knowledge = Mock()
    service = DemoService(store=store, knowledge=knowledge)
    assert not hasattr(service, "claims")
    store.find_claim.return_value = None
    with pytest.raises(ResourceNotFound):
        service.get_claim("clm-demo-001")
    store.find_claim.side_effect = ConnectionError("unreachable")
    with pytest.raises(ConnectionError):
        service.get_claim("clm-demo-001")
    store.find_policy.return_value = DemoService().get_policy("pol-demo-auto")
    knowledge.lookup.side_effect = ConnectionError("unreachable")
    with pytest.raises(ConnectionError):
        service.lookup_policy("pol-demo-auto", "deer")
    with pytest.raises(ValueError):
        DemoService(store=store)


def test_cloud_assessment_uses_live_data_and_citations() -> None:
    fixtures = DemoService()
    store = Mock()
    knowledge = Mock()
    store.find_claim.return_value = fixtures.get_claim("clm-demo-002")
    store.find_policy.return_value = fixtures.get_policy("pol-demo-auto")
    store.find_artifact.return_value = fixtures.artifacts["art-demo-estimate-002"]
    coverage_citations = fixtures.lookup_policy("pol-demo-auto", "collision")
    process_citations = fixtures.lookup_policy("pol-demo-auto", "Section 4 claims process")
    knowledge.lookup.side_effect = [coverage_citations, process_citations]
    assessment = DemoService(store=store, knowledge=knowledge).assess_claim("clm-demo-002")
    assert assessment.human_review_required
    assert assessment.policy_basis == "grounded"
    store.find_artifact.assert_called_once_with("art-demo-estimate-002")
    knowledge.lookup.assert_has_calls([
        call("pol-demo-auto", store.find_claim.return_value.claimant_statement),
        call("pol-demo-auto", "Section 4 claims process report 30 days theft vandalism police"),
    ])
    assert any(citation.section.startswith("Section 4") for citation in assessment.policy_citations)


def test_search_policy_filter_is_not_caller_controlled() -> None:
    client = Mock()
    client.get_document.return_value = {
        "citation_id": "cit-demo-001",
        "policy_id": "pol-demo-auto",
        "source_document": "data/policies/demo-auto-policy.md",
        "section": "Section 1",
        "quoted_text": "Synthetic evidence.",
    }
    rest = Mock()
    rest.request.return_value = {"references": [{"type": "searchIndex", "docKey": "cit-demo-001"}]}
    knowledge = SearchPolicyKnowledge(client, rest)
    assert knowledge.lookup("pol-demo-auto", "deer")[0].policy_id == "pol-demo-auto"
    body = rest.request.call_args.args[2]
    assert body["knowledgeSourceParams"][0]["filterAddOn"] == "policy_id eq 'pol-demo-auto'"
    assert body["intents"] == [{"type": "semantic", "search": "deer"}]
    client.get_document.assert_called_once_with(key="cit-demo-001")
    for policy_id, query in (("pol-a' or true", "query"), ("pol-demo-auto", " ")):
        with pytest.raises(ValueError):
            knowledge.lookup(policy_id, query)


@pytest.mark.parametrize(
    "references",
    [None, {}, ["not-an-object"], [{"type": "blob", "docKey": "cit-demo-001"}]],
)
def test_malformed_iq_references_are_explicit_dependency_errors(references) -> None:
    client, rest = Mock(), Mock()
    rest.request.return_value = {"references": references}
    with pytest.raises(KnowledgeError):
        SearchPolicyKnowledge(client, rest).lookup("pol-demo-auto", "animal")
    client.get_document.assert_not_called()


def test_iq_no_results_is_not_fabricated_and_cross_policy_evidence_is_rejected() -> None:
    client, rest = Mock(), Mock()
    knowledge = SearchPolicyKnowledge(client, rest)
    rest.request.return_value = {"references": []}
    assert knowledge.lookup("pol-demo-auto", "unrelated") == []
    client.get_document.assert_not_called()
    rest.request.return_value = {"references": [{"type": "searchIndex", "docKey": "cit-demo-001"}]}
    for document in (
        {},
        {
            "citation_id": "cit-demo-001",
            "policy_id": "pol-unrelated",
            "source_document": "other.md",
            "section": "Section 1",
            "quoted_text": "Other policy.",
        },
        {
            "citation_id": "cit-demo-wrong",
            "policy_id": "pol-demo-auto",
            "source_document": "other.md",
            "section": "Section 1",
            "quoted_text": "Other evidence.",
        },
    ):
        client.get_document.return_value = document
        with pytest.raises(KnowledgeError):
            knowledge.lookup("pol-demo-auto", "animal")


def cloud_values() -> dict[str, str]:
    return {
        "CLAIMS_MODE": "azure",
        "AZURE_TENANT_ID": "00000000-0000-0000-0000-000000000001",
        "CLAIMS_API_AUDIENCE": "00000000-0000-0000-0000-000000000002",
        "CLAIMS_API_CLIENT_IDS": "00000000-0000-0000-0000-000000000003",
        "AZURE_RUNTIME_CLIENT_ID": "00000000-0000-0000-0000-000000000004",
        "AZURE_SQL_SERVER": "demo.database.windows.net",
        "AZURE_SQL_DATABASE": "claims",
        "AZURE_SEARCH_ENDPOINT": "https://demo.search.windows.net",
        "AZURE_SEARCH_INDEX": "synthetic-policies",
        "CLAIMS_UI_CLIENT_ID": "00000000-0000-0000-0000-000000000003",
    }


def test_azure_mode_requires_complete_identity_and_dependency_configuration() -> None:
    values = cloud_values()
    assert Settings.from_environment(values).mode == "azure"
    for key in values:
        if key == "CLAIMS_MODE":
            continue
        with pytest.raises(ValueError):
            Settings.from_environment(
                {name: value for name, value in values.items() if name != key}
            )
    for invalid in (
        {"CLAIMS_MODE": "unexpected"},
        {"CONTAINER_APP_NAME": "deployed"},
        values | {"AZURE_SEARCH_ENDPOINT": "http://attacker.example"},
        values | {"CLAIMS_API_CLIENT_IDS": "not-a-uuid"},
        values | {"CLAIMS_UI_CLIENT_ID": "00000000-0000-0000-0000-000000000099"},
    ):
        with pytest.raises(ValueError):
            Settings.from_environment(invalid)
