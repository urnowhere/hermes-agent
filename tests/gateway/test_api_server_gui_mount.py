"""Tests for Hermes Web Console routes mounted by the API server adapter."""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter, cors_middleware
from gateway.web_console import static as web_console_static


def _make_adapter() -> APIServerAdapter:
    return APIServerAdapter(PlatformConfig(enabled=True))


def _create_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    adapter._register_routes(app)
    return app


class TestGuiRoutes:
    @pytest.mark.asyncio
    async def test_gui_health_route_exists(self):
        adapter = _make_adapter()
        app = _create_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/api/gui/health")
            assert resp.status == 200
            assert resp.content_type == "application/json"
            data = await resp.json()
            assert data == {
                "status": "ok",
                "service": "gui-backend",
                "product": "hermes-web-console",
            }

    @pytest.mark.asyncio
    async def test_gui_meta_route_exists(self):
        adapter = _make_adapter()
        app = _create_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/api/gui/meta")
            assert resp.status == 200
            data = await resp.json()
            assert data["product"] == "hermes-web-console"
            assert data["api_base_path"] == "/api/gui"
            assert data["app_base_path"] == "/app/"
            assert data["adapter"] == adapter.name

    @pytest.mark.asyncio
    async def test_app_placeholder_route_exists(self, tmp_path, monkeypatch):
        adapter = _make_adapter()
        monkeypatch.setattr(web_console_static, "_DIST_DIR", tmp_path / "missing-dist")
        app = _create_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/app/")
            assert resp.status == 200
            assert resp.content_type == "text/html"
            html = await resp.text()
            assert "Hermes Web Console" in html
            assert "/api/gui/health" in html
            assert "/api/gui/meta" in html

    @pytest.mark.asyncio
    async def test_app_serves_built_frontend_assets_when_dist_exists(self, tmp_path, monkeypatch):
        adapter = _make_adapter()
        dist_dir = tmp_path / "dist"
        assets_dir = dist_dir / "assets"
        assets_dir.mkdir(parents=True)
        (dist_dir / "index.html").write_text(
            """<!doctype html>
<html>
  <head>
    <link rel="manifest" href="/assets/manifest-abc.json" />
    <script type="module" src="/assets/app.js"></script>
    <link rel="stylesheet" href="/assets/app.css" />
  </head>
  <body>
    <script>navigator.serviceWorker.register('/sw.js')</script>
    <div id="root"></div>
  </body>
</html>
""",
            encoding="utf-8",
        )
        (assets_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")
        (assets_dir / "app.css").write_text("body{color:white;}", encoding="utf-8")

        frontend_root = tmp_path / "frontend"
        (frontend_root / "icons").mkdir(parents=True)
        (frontend_root / "manifest.json").write_text(
            json.dumps(
                {
                    "name": "Hermes Web Console",
                    "start_url": "/",
                    "icons": [{"src": "/icons/icon-192.png"}],
                }
            ),
            encoding="utf-8",
        )
        (frontend_root / "sw.js").write_text(
            "const STATIC_ASSETS = [\n  '/',\n  '/index.html',\n  '/manifest.json',\n];",
            encoding="utf-8",
        )
        (frontend_root / "icons" / "icon-192.png").write_bytes(b"png")

        monkeypatch.setattr(web_console_static, "_DIST_DIR", dist_dir)
        monkeypatch.setattr(web_console_static, "_WEB_CONSOLE_ROOT", frontend_root)

        app = _create_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            index_resp = await cli.get("/app/")
            assert index_resp.status == 200
            html = await index_resp.text()
            assert 'href="/app/manifest.json"' in html
            assert 'src="/app/assets/app.js"' in html
            assert 'href="/app/assets/app.css"' in html
            assert "navigator.serviceWorker.register('/app/sw.js')" in html

            asset_resp = await cli.get("/app/assets/app.js")
            assert asset_resp.status == 200
            assert "console.log('ok')" in await asset_resp.text()

            route_resp = await cli.get("/app/chat")
            assert route_resp.status == 200
            assert await route_resp.text() == html

            manifest_resp = await cli.get("/app/manifest.json")
            assert manifest_resp.status == 200
            manifest = await manifest_resp.json()
            assert manifest["start_url"] == "/app/"
            assert manifest["scope"] == "/app/"
            assert manifest["icons"][0]["src"] == "/app/icons/icon-192.png"

            sw_resp = await cli.get("/app/sw.js")
            assert sw_resp.status == 200
            sw_text = await sw_resp.text()
            assert "/app/index.html" in sw_text
            assert "/app/manifest.json" in sw_text

            icon_resp = await cli.get("/app/icons/icon-192.png")
            assert icon_resp.status == 200
            assert await icon_resp.read() == b"png"

    @pytest.mark.asyncio
    async def test_existing_routes_still_work_with_gui_mount(self):
        adapter = _make_adapter()
        app = _create_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            root_resp = await cli.get("/")
            health_resp = await cli.get("/health")
            models_resp = await cli.get("/v1/models")

            assert root_resp.status == 200
            assert health_resp.status == 200
            assert models_resp.status == 200

            health_data = await health_resp.json()
            models_data = await models_resp.json()

            assert health_data["status"] == "ok"
            assert models_data["data"][0]["id"] == "hermes-agent"
