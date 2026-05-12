"""
Integration tests for the /search endpoint.

These test the HTTP layer: correct status codes, auth, query param handling.
The actual search logic is tested in test_search_router.py and test_hybrid_search.py.
"""


class TestSearchEndpoint:
    def test_requires_auth(self, client):
        resp = client.get("/search/semantic?query=test")
        assert resp.status_code == 401

    def test_min_query_length(self, client, auth_headers):
        resp = client.get("/search/semantic?query=ab", headers=auth_headers)
        assert resp.status_code == 422  # Validation error

    def test_returns_articles_and_highlights(self, client, auth_headers):
        resp = client.get("/search/semantic?query=test query", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "articles" in data
        assert "highlights" in data
        assert isinstance(data["articles"], list)
        assert isinstance(data["highlights"], list)

    def test_filter_query_works(self, client, auth_headers, test_content):
        resp = client.get(
            "/search/semantic?query=author:Test Author",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "articles" in data

    def test_keyword_query_works(self, client, auth_headers, test_content):
        resp = client.get(
            '/search/semantic?query="Test Article"',
            headers=auth_headers,
        )
        assert resp.status_code == 200
