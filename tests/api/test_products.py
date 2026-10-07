"""Tests for products endpoints."""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_db_list(sample_product, sample_competitor):
    """Mock database for list endpoint."""
    mock_client = MagicMock()

    products_response = MagicMock()
    products_response.data = [sample_product]
    products_response.count = 1

    competitors_response = MagicMock()
    competitors_response.data = [sample_competitor]

    def mock_table(name):
        table_mock = MagicMock()
        if name == "products":
            table_mock.select.return_value.eq.return_value.order.return_value.execute.return_value = products_response
        elif name == "competitors":
            table_mock.select.return_value.eq.return_value.execute.return_value = competitors_response
        return table_mock

    mock_client.table = mock_table

    with patch("app.api.routes.products.get_supabase_client", return_value=mock_client):
        yield mock_client


@pytest.fixture
def mock_db_get(sample_product, sample_competitor):
    """Mock database for get single product."""
    mock_client = MagicMock()

    product_response = MagicMock()
    product_response.data = [sample_product]

    competitors_response = MagicMock()
    competitors_response.data = [sample_competitor]

    def mock_table(name):
        table_mock = MagicMock()
        if name == "products":
            table_mock.select.return_value.eq.return_value.eq.return_value.execute.return_value = product_response
        elif name == "competitors":
            table_mock.select.return_value.eq.return_value.execute.return_value = competitors_response
        return table_mock

    mock_client.table = mock_table

    with patch("app.api.routes.products.get_supabase_client", return_value=mock_client):
        yield mock_client


@pytest.fixture
def mock_db_not_found():
    """Mock database returning no product."""
    mock_client = MagicMock()

    empty_response = MagicMock()
    empty_response.data = []

    def mock_table(name):
        table_mock = MagicMock()
        table_mock.select.return_value.eq.return_value.eq.return_value.execute.return_value = empty_response
        return table_mock

    mock_client.table = mock_table

    with patch("app.api.routes.products.get_supabase_client", return_value=mock_client):
        yield mock_client


class TestListProducts:
    """Tests for GET /api/products."""

    def test_list_products_success(self, client: TestClient, mock_auth, mock_db_list):
        """Successfully list user's products."""
        response = client.get(
            "/api/products",
            headers={"Authorization": "Bearer mock-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "products" in data
        assert "total" in data
        assert data["total"] == 1
        assert len(data["products"]) == 1
        assert data["products"][0]["product_name"] == "Test Product"

    def test_list_products_no_auth(self, client: TestClient):
        """Return 401 without auth."""
        response = client.get("/api/products")
        assert response.status_code == 401


class TestGetProduct:
    """Tests for GET /api/products/{product_id}."""

    def test_get_product_success(self, client: TestClient, mock_auth, mock_db_get):
        """Successfully get a single product."""
        response = client.get(
            "/api/products/prod-uuid-1234",
            headers={"Authorization": "Bearer mock-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "prod-uuid-1234"
        assert data["product_name"] == "Test Product"
        assert len(data["competitors"]) == 1

    def test_get_product_not_found(self, client: TestClient, mock_auth, mock_db_not_found):
        """Return 404 for non-existent product."""
        response = client.get(
            "/api/products/nonexistent",
            headers={"Authorization": "Bearer mock-token"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Product not found"

    def test_get_product_no_auth(self, client: TestClient):
        """Return 401 without auth."""
        response = client.get("/api/products/prod-uuid-1234")
        assert response.status_code == 401


class TestUpdateProduct:
    """Tests for PUT /api/products/{product_id}."""

    def test_update_product_success(
        self, client: TestClient, mock_auth, sample_product, sample_competitor
    ):
        """Successfully update product name and status."""
        mock_client = MagicMock()
        existing_resp = MagicMock()
        existing_resp.data = [{"id": "prod-uuid-1234"}]

        updated_product = dict(sample_product)
        updated_product["product_name"] = "Updated Name"
        updated_product["is_active"] = False

        update_resp = MagicMock()
        update_resp.data = [updated_product]

        competitors_resp = MagicMock()
        competitors_resp.data = [sample_competitor]

        def mock_table(name):
            table_mock = MagicMock()
            if name == "products":
                table_mock.select.return_value.eq.return_value.eq.return_value.execute.return_value = existing_resp
                table_mock.update.return_value.eq.return_value.eq.return_value.execute.return_value = update_resp
            elif name == "competitors":
                table_mock.select.return_value.eq.return_value.execute.return_value = competitors_resp
            return table_mock

        mock_client.table = mock_table

        with patch("app.api.routes.products.get_supabase_client", return_value=mock_client):
            response = client.put(
                "/api/products/prod-uuid-1234",
                headers={"Authorization": "Bearer mock-token"},
                json={"product_name": "Updated Name", "is_active": False},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["product_name"] == "Updated Name"
        assert data["is_active"] is False

    def test_update_product_no_fields(self, client: TestClient, mock_auth, mock_db_get):
        """Return 400 when no fields to update."""
        response = client.put(
            "/api/products/prod-uuid-1234",
            headers={"Authorization": "Bearer mock-token"},
            json={},
        )

        assert response.status_code == 400
        assert "No fields to update" in response.json()["detail"]

    def test_update_product_not_found(self, client: TestClient, mock_auth, mock_db_not_found):
        """Return 404 for non-existent product."""
        response = client.put(
            "/api/products/nonexistent",
            headers={"Authorization": "Bearer mock-token"},
            json={"product_name": "New Name"},
        )

        assert response.status_code == 404

    def test_update_product_no_auth(self, client: TestClient):
        """Return 401 without auth."""
        response = client.put(
            "/api/products/prod-uuid-1234",
            json={"product_name": "New Name"},
        )
        assert response.status_code == 401


class TestDeleteProduct:
    """Tests for DELETE /api/products/{product_id}."""

    def test_delete_product_success(self, client: TestClient, mock_auth):
        """Successfully soft delete product (returns 204)."""
        mock_client = MagicMock()
        existing_resp = MagicMock()
        existing_resp.data = [{"id": "prod-uuid-1234"}]

        delete_resp = MagicMock()
        delete_resp.data = [{"id": "prod-uuid-1234", "is_active": False}]

        def mock_table(name):
            table_mock = MagicMock()
            if name == "products":
                table_mock.select.return_value.eq.return_value.eq.return_value.execute.return_value = existing_resp
                table_mock.update.return_value.eq.return_value.eq.return_value.execute.return_value = delete_resp
            return table_mock

        mock_client.table = mock_table

        with patch("app.api.routes.products.get_supabase_client", return_value=mock_client):
            response = client.delete(
                "/api/products/prod-uuid-1234",
                headers={"Authorization": "Bearer mock-token"},
            )

        assert response.status_code == 204

    def test_delete_product_not_found(self, client: TestClient, mock_auth, mock_db_not_found):
        """Return 404 for non-existent product."""
        response = client.delete(
            "/api/products/nonexistent",
            headers={"Authorization": "Bearer mock-token"},
        )

        assert response.status_code == 404

    def test_delete_product_no_auth(self, client: TestClient):
        """Return 401 without auth."""
        response = client.delete("/api/products/prod-uuid-1234")
        assert response.status_code == 401
