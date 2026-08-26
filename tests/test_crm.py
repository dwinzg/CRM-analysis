"""CrmClient tests against a stubbed transport. No network."""
import httpx
import pytest

from bellhaven.crm import CrmClient, account_id_of


def client_with(handler):
    c = CrmClient(token="test", base="https://example.test/api/v1")
    c._c = httpx.Client(transport=httpx.MockTransport(handler),
                        headers={"Authorization": "Bearer test"})
    return c


def test_pagination_follows_every_page():
    def handler(request):
        page = int(dict(request.url.params).get("page", 1))
        data = [{"account_id": f"A{page}-{i}"} for i in range(200)] if page < 3 else \
               [{"account_id": "A3-0"}]
        return httpx.Response(200, json={"data": data, "page": page,
                                         "page_size": 200, "total": 401})
    assert len(client_with(handler).list_accounts()) == 401


def test_pagination_keeps_going_when_total_is_missing():
    """The API declares no response schema. If `total` is absent or renamed,
    stopping after one page means reasoning over a partial CRM: spurious
    CREATE proposals for facilities that already exist, and duplicate clusters
    split across the page boundary going undetected."""
    def handler(request):
        page = int(dict(request.url.params).get("page", 1))
        data = [{"account_id": f"A{page}-{i}"} for i in range(200)] if page == 1 else \
               [{"account_id": "A2-0"}]
        return httpx.Response(200, json={"data": data})   # no `total`
    assert len(client_with(handler).list_accounts()) == 201


def test_pagination_stops_on_a_short_page():
    def handler(request):
        return httpx.Response(200, json={"data": [{"account_id": "only"}]})
    assert len(client_with(handler).list_accounts()) == 1


def test_pagination_stops_on_an_empty_page():
    def handler(request):
        page = int(dict(request.url.params).get("page", 1))
        data = [{"account_id": f"A{i}"} for i in range(200)] if page == 1 else []
        return httpx.Response(200, json={"data": data})
    assert len(client_with(handler).list_accounts()) == 200


def test_account_id_of_accepts_the_documented_alternatives():
    assert account_id_of({"account_id": "X"}) == "X"
    assert account_id_of({"id": "Y"}) == "Y"
    with pytest.raises(KeyError):
        account_id_of({"message": "created"})
