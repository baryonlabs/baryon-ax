"""Open-source inventory endpoint."""

from fastapi.testclient import TestClient

from baryon.main import app

client = TestClient(app)


def test_open_source_inventory_is_served() -> None:
    response = client.get("/v1/open-source")
    assert response.status_code == 200
    payload = response.json()

    assert payload["summary"]["total"] == len(payload["data"])
    assert payload["summary"]["active"] > 0
    assert payload["docs_url"].startswith("https://github.com/baryonlabs/baryon-ax/")

    ids = {component["id"] for component in payload["data"]}
    assert {"fastapi", "react", "qdrant", "kordoc", "docling", "langfuse"} <= ids


def test_every_component_has_license_and_link() -> None:
    payload = client.get("/v1/open-source").json()
    for component in payload["data"]:
        assert component["name"]
        assert component["license"]
        assert component["role"]
        assert component["url"].startswith("https://")
        assert component["status"] in {"active", "optional", "test"}
