"""Dependency boundaries that keep business modules independently testable."""

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_adapters_and_utilities_do_not_import_application_layer():
    failures = []
    for package in ("steam", "buff", "steamdt", "analysis", "utils"):
        for path in (ROOT / package).rglob("*.py"):
            for module in imports(path):
                if module == "app" or module.startswith("app."):
                    failures.append(f"{path.relative_to(ROOT)} -> {module}")
    assert not failures, "Inject application dependencies instead: " + "; ".join(failures)


def test_services_and_business_modules_do_not_depend_on_http_routes():
    failures = []
    for package in (
        "accounts",
        "background",
        "configuration",
        "maintenance",
        "notifications",
        "purchase",
        "runtime",
        "services",
        "steam_market",
        "storage",
        "strategies",
        "workflows",
    ):
        for path in (ROOT / "app" / package).rglob("*.py"):
            for module in imports(path):
                if module.startswith(("app.routes", "fastapi")):
                    failures.append(f"{path.relative_to(ROOT)} -> {module}")
    assert not failures, "Keep HTTP handling in routes: " + "; ".join(failures)


def test_application_root_contains_only_entry_modules():
    allowed = {"__init__.py", "__main__.py", "api.py", "main.py", "runtime_env.py"}
    actual = {path.name for path in (ROOT / "app").glob("*.py")}
    assert actual == allowed


def test_web_runtime_does_not_depend_on_desktop_shell():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]
    assert not any(dependency.lower().startswith("pywebview") for dependency in dependencies)

    launcher_modules = {module.split(".", 1)[0] for module in imports(ROOT / "app" / "main.py")}
    assert launcher_modules.isdisjoint({"webview", "webbrowser"}), (
        "Keep the entry point a pure web server: " + ", ".join(sorted(launcher_modules))
    )


def test_business_package_initializers_do_not_reexport_implementations():
    failures = []
    for path in (ROOT / "app").glob("*/__init__.py"):
        if path.parent.name == "routes":
            continue  # HTTP router registration belongs to this package entry.
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                failures.append(str(path.relative_to(ROOT)))
    assert not failures, "Import the owning module directly: " + "; ".join(failures)


def test_services_do_not_define_http_request_models():
    failures = []
    for path in (ROOT / "app" / "services").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(
                isinstance(base, ast.Name) and base.id == "BaseModel"
                for base in node.bases
            ):
                failures.append(f"{path.relative_to(ROOT)}:{node.name}")
    assert not failures, "Place HTTP request models in routes: " + "; ".join(failures)


def test_services_do_not_import_workflows():
    failures = []
    for path in (ROOT / "app" / "services").glob("*.py"):
        for module in imports(path):
            if module.startswith("app.workflows"):
                failures.append(f"{path.relative_to(ROOT)} -> {module}")
    assert not failures, "Wire workflow callbacks in app.api or workflows: " + "; ".join(failures)


def test_application_wires_auth_resume_to_purchase_workflow():
    from app import api  # noqa: F401 -- importing assembles application callbacks
    from app.services import auth_session
    from app.workflows.purchase import start_pipeline

    assert auth_session._start_purchase_pipeline is start_pipeline
