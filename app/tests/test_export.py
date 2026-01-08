"""Tests for CSV export endpoint."""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from decimal import Decimal

from main import app
from app.core.security import get_current_user, verify_token
from app.db.database import get_supabase_client


client = TestClient(app)


@pytest.fixture
def mock_auth(mock_user):
    """Override authentication."""
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[verify_token] = lambda: mock_user
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def mock_db_success(sample_product, sample_competitor, sample_price_history):
    """Mock successful database responses."""
    mock_client = MagicMock()

    product_response = MagicMock()
    product_response.data = [sample_product]

    competitor_response = MagicMock()
    competitor_response.data = [sample_competitor]

    price_response = MagicMock()
    price_response.data = sample_price_history

    def mock_table(name):
        table_mock = MagicMock()
        if name == "products":
            table_mock.select.return_value.eq.return_value.eq.return_value.execute.return_value = product_response
        elif name == "competitors":
            table_mock.select.return_value.eq.return_value.execute.return_value = competitor_response
        elif name == "price_history":
            table_mock.select.return_value.in_.return_value.order.return_value.execute.return_value = price_response
        return table_mock

    mock_client.table = mock_table

    with patch("app.api.routes.export.get_supabase_client", return_value=mock_client):
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

    with patch("app.api.routes.export.get_supabase_client", return_value=mock_client):
        yield mock_client


class TestExportCSV:
    """Tests for CSV export functionality."""

    def test_export_csv_success(self, mock_auth, mock_db_success):
        """Successfully export price history as CSV."""
        response = client.get(
            "/api/export/prod-uuid-1234/csv",
            headers={"Authorization": "Bearer mock-token"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert "attachment" in response.headers["content-disposition"]
        assert "price_history_" in response.headers["content-disposition"]
        assert ".csv" in response.headers["content-disposition"]

        content = response.text
        assert "Date,Competitor,Price,Currency,Status,Error" in content
        assert "Example Store" in content

    def test_export_csv_product_not_found(self, mock_auth, mock_db_not_found):
        """Return 404 when product doesn't exist."""
        response = client.get(
            "/api/export/nonexistent-uuid/csv",
            headers={"Authorization": "Bearer mock-token"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Product not found"

    def test_export_csv_no_auth(self):
        """Return 401 when no auth header provided."""
        response = client.get("/api/export/prod-uuid-1234/csv")
        assert response.status_code == 401

    def test_csv_filename_has_product_name(self, mock_auth, mock_db_success):
        """Filename contains product name and date."""
        response = client.get(
            "/api/export/prod-uuid-1234/csv",
            headers={"Authorization": "Bearer mock-token"},
        )

        disposition = response.headers["content-disposition"]
        assert "Test Product" in disposition
        assert "price_history_" in disposition
        assert ".csv" in disposition


class TestCSVContent:
    """Tests for CSV content formatting."""

    def test_csv_has_correct_headers(self, mock_auth, mock_db_success):
        """CSV contains expected column headers."""
        response = client.get(
            "/api/export/prod-uuid-1234/csv",
            headers={"Authorization": "Bearer mock-token"},
        )

        lines = response.text.strip().split("\n")
        headers = lines[0]
        assert "Date" in headers
        assert "Competitor" in headers
        assert "Price" in headers
        assert "Currency" in headers
        assert "Status" in headers

    def test_csv_data_rows(self, mock_auth, mock_db_success):
        """CSV contains data rows from price history."""
        response = client.get(
            "/api/export/prod-uuid-1234/csv",
            headers={"Authorization": "Bearer mock-token"},
        )

        lines = response.text.strip().split("\n")
        assert len(lines) >= 2  # header + at least one data row
