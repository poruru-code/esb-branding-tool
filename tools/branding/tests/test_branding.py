# Where: tools/branding/tests/test_branding.py
# What: Tests for branding derivation and template rendering.
# Why: Ensure generator inputs and substitutions stay consistent.
from __future__ import annotations

from pathlib import Path

import pytest

from tools.branding.branding import BrandingError, build_context, derive_branding
from tools.branding.generate import (
    load_brand_from_config,
    render_string,
    resolve_brand,
)


def test_load_branding_builds_context() -> None:
    branding = derive_branding("esb")
    context = build_context(branding)
    assert context["CLI_NAME"] == "esb"
    assert context["ENV_PREFIX"] == "ESB"
    assert context["RUNTIME_CNI_BRIDGE"] == "esb0"


def test_render_string_replaces_placeholders() -> None:
    rendered = render_string("run {{CLI_NAME}}", {"CLI_NAME": "esb"})
    assert rendered == "run esb"


def test_render_string_rejects_unknown_keys() -> None:
    with pytest.raises(BrandingError):
        render_string("{{MISSING}}", {})


def test_compose_templates_include_proxy_ca_secret_wiring() -> None:
    branding = derive_branding("esb")
    context = build_context(branding)
    template_expectations = {
        Path("tools/branding/templates/docker-compose.docker.yml.tmpl"): (
            "  proxy_ca:\n    file: ${PROXY_CA_CERT_FILE:-${CERT_DIR:-./.esb/certs}/rootCA.crt}"
        ),
        Path("tools/branding/templates/docker-compose.containerd.yml.tmpl"): (
            "  proxy_ca:\n    file: ${PROXY_CA_CERT_FILE:-${CERT_DIR:-./.esb/certs}/rootCA.crt}"
        ),
        Path("tools/branding/templates/docker-compose.fc.yml.tmpl"): (
            "  proxy_ca:\n    file: ${PROXY_CA_CERT_FILE:-${CERT_DIR:-./.esb/certs}/rootCA.crt}"
        ),
        Path("tools/branding/templates/docker-compose.fc-node.yml.tmpl"): (
            "  proxy_ca:\n    file: ${PROXY_CA_CERT_FILE:-${CERT_DIR:-./.esb/certs}/rootCA.crt}"
        ),
    }

    for template_path, expected_secret_line in template_expectations.items():
        rendered = render_string(template_path.read_text(encoding="utf-8"), context)
        assert "PROXY_CA_MOUNT_ID: proxy_ca" in rendered
        assert rendered.count("- proxy_ca") >= 2
        assert expected_secret_line in rendered


def test_load_brand_from_config_reads_brand(tmp_path) -> None:
    path = tmp_path / "branding.yaml"
    path.write_text("# comment\nbrand: acme # trailing\n", encoding="utf-8")
    assert load_brand_from_config(path) == "acme"


def test_resolve_brand_prefers_config(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "branding.yaml").write_text("brand: esb\n", encoding="utf-8")
    assert resolve_brand(None, tmp_path) == "esb"


def test_resolve_brand_does_not_write_config_when_missing_with_cli_override(tmp_path) -> None:
    assert resolve_brand("esb", tmp_path) == "esb"
    assert not (tmp_path / "config" / "branding.yaml").exists()


def test_resolve_brand_does_not_update_config_on_mismatch_with_cli_override(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "branding.yaml"
    config_path.write_text("brand: esb\n", encoding="utf-8")
    assert resolve_brand("acme", tmp_path) == "acme"
    assert "brand: esb" in config_path.read_text(encoding="utf-8")


def test_resolve_brand_rejects_mismatch_in_check_mode(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "branding.yaml").write_text("brand: esb\n", encoding="utf-8")
    with pytest.raises(BrandingError):
        resolve_brand("acme", tmp_path, check=True)


def test_resolve_brand_requires_config_in_check_mode(tmp_path) -> None:
    assert resolve_brand("esb", tmp_path, check=True) == "esb"


def test_resolve_brand_requires_input_in_check_mode(tmp_path) -> None:
    with pytest.raises(BrandingError):
        resolve_brand(None, tmp_path, check=True)


def test_resolve_brand_requires_input(tmp_path) -> None:
    with pytest.raises(BrandingError):
        resolve_brand(None, tmp_path)


def test_deployops_branding_constants_template_renders_brand_paths() -> None:
    branding = derive_branding("esb")
    context = build_context(branding)
    template_path = Path("tools/branding/templates/pkg/deployops/branding_constants_gen.go.tmpl")
    rendered = render_string(template_path.read_text(encoding="utf-8"), context)
    assert 'defaultBrandSlug    = "esb"' in rendered
    assert 'defaultBrandHomeDir = "." + defaultBrandSlug' in rendered
    assert 'defaultBrandCertDir = defaultBrandHomeDir + "/certs"' in rendered
