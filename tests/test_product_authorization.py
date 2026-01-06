"""
Test suite for product authorization and RLS enforcement.

Tests both application-level and database-level security.
"""

import pytest
from fastapi.testclient import TestClient

# These tests require:
# 1. RLS policies applied in Supabase (run rls_policies.sql)
# 2. Two test users created in Supabase auth.users
# 3. Access tokens for both users


@pytest.fixture
def user1_token():
    """Replace with actual token for test user 1."""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."  # Get from Supabase


@pytest.fixture
def user2_token():
    """Replace with actual token for test user 2."""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."  # Get from Supabase


def test_user_cannot_view_other_users_products(client: TestClient, user1_token, user2_token):
    """Test that user 2 cannot access user 1's products."""

    # User 1 creates a product
    response = client.post(
        "/api/stores/track",
        headers={"Authorization": f"Bearer {user1_token}"},
        json={
            "group_name": "User 1 Product",
            "product_urls": ["https://example.com/product1"],
            "alert_threshold_percent": 10.0
        }
    )
    assert response.status_code == 201
    product_id = response.json()["group_id"]

    # User 2 tries to access user 1's product (should fail)
    response = client.get(
        f"/api/products/{product_id}",
        headers={"Authorization": f"Bearer {user2_token}"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Product not found"


def test_user_cannot_update_other_users_products(client: TestClient, user1_token, user2_token):
    """Test that user 2 cannot update user 1's products."""

    # User 1 creates a product
    response = client.post(
        "/api/stores/track",
        headers={"Authorization": f"Bearer {user1_token}"},
        json={
            "group_name": "User 1 Product",
            "product_urls": ["https://example.com/product1"],
            "alert_threshold_percent": 10.0
        }
    )
    assert response.status_code == 201
    product_id = response.json()["group_id"]

    # User 2 tries to update user 1's product (should fail)
    response = client.put(
        f"/api/products/{product_id}",
        headers={"Authorization": f"Bearer {user2_token}"},
        json={"product_name": "Hacked Name"}
    )
    assert response.status_code == 404


def test_user_cannot_delete_other_users_products(client: TestClient, user1_token, user2_token):
    """Test that user 2 cannot delete user 1's products."""

    # User 1 creates a product
    response = client.post(
        "/api/stores/track",
        headers={"Authorization": f"Bearer {user1_token}"},
        json={
            "group_name": "User 1 Product",
            "product_urls": ["https://example.com/product1"],
            "alert_threshold_percent": 10.0
        }
    )
    assert response.status_code == 201
    product_id = response.json()["group_id"]

    # User 2 tries to delete user 1's product (should fail)
    response = client.delete(
        f"/api/products/{product_id}",
        headers={"Authorization": f"Bearer {user2_token}"}
    )
    assert response.status_code == 404


def test_user_can_only_list_own_products(client: TestClient, user1_token, user2_token):
    """Test that users only see their own products in listings."""

    # User 1 creates a product
    response = client.post(
        "/api/stores/track",
        headers={"Authorization": f"Bearer {user1_token}"},
        json={
            "group_name": "User 1 Product",
            "product_urls": ["https://example.com/product1"],
            "alert_threshold_percent": 10.0
        }
    )
    assert response.status_code == 201

    # User 2 creates a product
    response = client.post(
        "/api/stores/track",
        headers={"Authorization": f"Bearer {user2_token}"},
        json={
            "group_name": "User 2 Product",
            "product_urls": ["https://example.com/product2"],
            "alert_threshold_percent": 10.0
        }
    )
    assert response.status_code == 201

    # User 1 lists products (should only see their own)
    response = client.get(
        "/api/products",
        headers={"Authorization": f"Bearer {user1_token}"}
    )
    assert response.status_code == 200
    products = response.json()["products"]
    assert all(p["product_name"] == "User 1 Product" for p in products)

    # User 2 lists products (should only see their own)
    response = client.get(
        "/api/products",
        headers={"Authorization": f"Bearer {user2_token}"}
    )
    assert response.status_code == 200
    products = response.json()["products"]
    assert all(p["product_name"] == "User 2 Product" for p in products)


def test_user_can_manage_own_products(client: TestClient, user1_token):
    """Test that users can perform CRUD on their own products."""

    # Create
    response = client.post(
        "/api/stores/track",
        headers={"Authorization": f"Bearer {user1_token}"},
        json={
            "group_name": "My Product",
            "product_urls": ["https://example.com/product1"],
            "alert_threshold_percent": 10.0
        }
    )
    assert response.status_code == 201
    product_id = response.json()["group_id"]

    # Read
    response = client.get(
        f"/api/products/{product_id}",
        headers={"Authorization": f"Bearer {user1_token}"}
    )
    assert response.status_code == 200
    assert response.json()["product_name"] == "My Product"

    # Update
    response = client.put(
        f"/api/products/{product_id}",
        headers={"Authorization": f"Bearer {user1_token}"},
        json={"product_name": "Updated Product"}
    )
    assert response.status_code == 200
    assert response.json()["product_name"] == "Updated Product"

    # Delete
    response = client.delete(
        f"/api/products/{product_id}",
        headers={"Authorization": f"Bearer {user1_token}"}
    )
    assert response.status_code == 204


# Manual Testing Instructions (if automated tests are not set up):
"""
1. Create two test users in Supabase:
   - Go to Authentication → Users → Add user
   - Create user1@test.com and user2@test.com

2. Get access tokens for both users:
   curl -X POST "https://YOUR_PROJECT.supabase.co/auth/v1/token?grant_type=password" \
     -H "apikey: YOUR_ANON_KEY" \
     -H "Content-Type: application/json" \
     -d '{"email": "user1@test.com", "password": "password123"}'

3. Test in test.http or Postman:

   # User 1 creates product
   POST http://localhost:8000/api/stores/track
   Authorization: Bearer USER1_TOKEN
   {"group_name": "Test Product", "product_urls": [...], ...}

   # Copy the group_id from response

   # User 2 tries to access (should fail with 404)
   GET http://localhost:8000/api/products/{group_id}
   Authorization: Bearer USER2_TOKEN

   # Expected: 404 Not Found

4. Verify RLS is working:
   - Go to Supabase SQL Editor
   - Run: SELECT * FROM products;
   - Should see products from all users (you're using service key)
   - RLS is enforced at client level with user tokens
"""
