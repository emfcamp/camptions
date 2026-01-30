"""Tests for API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    """Test the health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_list_venues_empty(client: AsyncClient):
    """Test listing venues when none exist."""
    response = await client.get("/api/venues")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_venue(client: AsyncClient):
    """Test creating a new venue."""
    venue_data = {
        "id": "test-stage",
        "name": "Test Stage",
        "description": "A test venue",
    }
    response = await client.post("/api/venues", json=venue_data)
    assert response.status_code == 200

    data = response.json()
    assert data["id"] == "test-stage"
    assert data["name"] == "Test Stage"
    assert data["description"] == "A test venue"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_get_venue(client: AsyncClient):
    """Test getting a specific venue."""
    # First create a venue
    venue_data = {
        "id": "stage-a",
        "name": "Stage A",
        "description": "Main stage",
    }
    await client.post("/api/venues", json=venue_data)

    # Then get it
    response = await client.get("/api/venues/stage-a")
    assert response.status_code == 200

    data = response.json()
    assert data["id"] == "stage-a"
    assert data["name"] == "Stage A"


@pytest.mark.asyncio
async def test_get_venue_not_found(client: AsyncClient):
    """Test getting a non-existent venue."""
    response = await client.get("/api/venues/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_caption_history_empty(client: AsyncClient):
    """Test getting caption history when none exist."""
    # First create a venue
    venue_data = {"id": "stage-a", "name": "Stage A"}
    await client.post("/api/venues", json=venue_data)

    response = await client.get("/api/captions/history/stage-a")
    assert response.status_code == 200

    data = response.json()
    assert data["venue_id"] == "stage-a"
    assert data["count"] == 0
    assert data["segments"] == []


@pytest.mark.asyncio
async def test_admin_stats(client: AsyncClient):
    """Test the admin stats endpoint."""
    response = await client.get("/api/admin/stats")
    assert response.status_code == 200

    data = response.json()
    assert "venues" in data
    assert "sessions" in data
    assert "segments" in data
    assert "subscribers" in data


@pytest.mark.asyncio
async def test_init_default_venues(client: AsyncClient):
    """Test initializing default venues."""
    response = await client.post("/api/admin/init-venues")
    assert response.status_code == 200

    data = response.json()
    assert "created" in data
    assert "existing" in data
    assert len(data["created"]) > 0


@pytest.mark.asyncio
async def test_list_sessions_empty(client: AsyncClient):
    """Test listing sessions when none exist."""
    response = await client.get("/api/admin/sessions")
    assert response.status_code == 200

    data = response.json()
    assert data["count"] == 0
    assert data["sessions"] == []
