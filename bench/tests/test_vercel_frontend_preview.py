import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VERCEL_FRONTEND = REPO_ROOT / "vercel-frontend"


def test_vercel_preview_serves_branch_local_ui_assets(tmp_path: Path):
    source = tmp_path / "web"
    templates = source / "templates"
    static = source / "static" / "js"
    templates.mkdir(parents=True)
    static.mkdir(parents=True)
    (templates / "index.html").write_text(
        "<!doctype html><html><head></head><body>"
        '<script src="/static/js/app.js"></script>'
        "</body></html>",
        encoding="utf-8",
    )
    (static / "app.js").write_text("console.log('branch ui');\n", encoding="utf-8")

    output = tmp_path / "public"
    env = {
        **os.environ,
        "QTB_WEB_SOURCE_DIR": str(source),
        "QTB_VERCEL_OUTPUT_DIR": str(output),
        "VERCEL_ENV": "preview",
    }

    subprocess.run(
        ["node", "scripts/build-static-preview.js"],
        cwd=VERCEL_FRONTEND,
        env=env,
        check=True,
    )

    index = (output / "index.html").read_text(encoding="utf-8")
    assert "window.QTB.staticPreview = true" in index
    assert (output / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    ) == "console.log('branch ui');\n"


def test_vercel_production_build_omits_static_preview_flag(tmp_path: Path):
    source = tmp_path / "web"
    templates = source / "templates"
    static = source / "static"
    templates.mkdir(parents=True)
    static.mkdir(parents=True)
    (templates / "index.html").write_text(
        "<!doctype html><html><head></head><body></body></html>",
        encoding="utf-8",
    )
    (static / ".gitkeep").write_text("", encoding="utf-8")

    output = tmp_path / "public"
    env = {
        **os.environ,
        "QTB_WEB_SOURCE_DIR": str(source),
        "QTB_VERCEL_OUTPUT_DIR": str(output),
        "VERCEL_ENV": "production",
    }

    subprocess.run(
        ["node", "scripts/build-static-preview.js"],
        cwd=VERCEL_FRONTEND,
        env=env,
        check=True,
    )

    index = (output / "index.html").read_text(encoding="utf-8")
    assert "staticPreview" not in index


def test_vercel_rewrites_keep_frontend_local_and_backend_proxied():
    config = json.loads((VERCEL_FRONTEND / "vercel.json").read_text())
    rewrites = {
        item["source"]: item["destination"]
        for item in config["rewrites"]
    }

    assert rewrites["/review"] == "/index.html"
    assert rewrites["/review/:path*"] == "/index.html"
    assert "/" not in rewrites
    assert "/static/:path*" not in rewrites

    backend = "https://217-15-165-83.sslip.io"
    for route in (
        "/health",
        "/auth/:path*",
        "/session/:path*",
        "/client/:path*",
        "/ui/:path*",
        "/skill.md",
        "/skills/:path*",
        "/mcp/:path*",
        "/mcp",
    ):
        assert rewrites[route].startswith(backend)
