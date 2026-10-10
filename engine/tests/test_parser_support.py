"""Tests for the TIBCO parser support modules (xmlutil, substvar, resources, contracts)."""
from pathlib import Path

import pytest

from engine.tibco_parser import contracts, resources, substvar, xmlutil

REPO_ROOT = Path(__file__).resolve().parents[2]
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
PUBLIC = REPO_ROOT / "samples" / "public"

ORIG_RES = GMF / "CreditOrigination" / "Resources" / "creditorigination"


def test_substvar_values():
    by_name = {v.name: v for v in substvar.parse_substvar(GMF / "CreditOrigination/META-INF/default.substvar")}
    assert by_name["GMF.DB.URL"].value == "jdbc:postgresql://localhost:5432/gmf"
    assert by_name["EQUIFAX.PORT"].value == "8091"
    assert by_name["EQUIFAX.PORT"].type == "Integer"
    assert by_name["BW.DEPLOYMENTUNIT.TYPE"].value == ""
    assert by_name["GMF.DB.URL"].source_path.endswith("CreditOrigination/META-INF/default.substvar")


def test_substvar_bw5_default_vars():
    by_name = {v.name: v for v in substvar.parse_substvar(GMF / "LegacyAddress/defaultVars/defaultVars.substvar")}
    assert by_name["AddressServicePort"].value == "8095"
    assert by_name["AddressServicePort"].type == "Integer"
    assert by_name["HttpHost"].value == "localhost"


def test_jdbc_resource_password_masked():
    r = resources.parse_resource(ORIG_RES / "GMFOriginationDB.jdbcResource")
    assert r.kind == "jdbc"
    assert r.id == "CreditOrigination.creditorigination.GMFOriginationDB"
    assert r.name == "GMFOriginationDB"
    assert r.settings["password"] == "***"
    assert "password" in r.masked_fields
    assert "iGRPsdsY7kAgcW8UeJflC" not in str(r.model_dump())
    assert r.settings["username"] == "svc_bw_orig"
    assert r.settings["driver"] == "org.postgresql.Driver"
    assert r.settings["jdbc_url"] == "jdbc:postgresql://localhost:5432/gmf"
    assert r.settings["max_connections"] == 10
    assert r.settings["substitution_bindings"]["dbURL"] == "GMF.DB.URL"


@pytest.mark.parametrize("name,port_var", [("EquifaxClient", "EQUIFAX.PORT"), ("TransUnionClient", "TRANSUNION.PORT")])
def test_http_resource_resilience_settings(name, port_var):
    r = resources.parse_resource(ORIG_RES / f"{name}.httpClientResource")
    assert r.kind == "http"
    assert r.settings["timeout_ms"] == 1000
    assert r.settings["circuit_breaker_failure_rate"] == 50
    assert r.settings["circuit_breaker_window"] == 20
    assert r.settings["port"] == (8091 if name == "EquifaxClient" else 8092)
    assert r.settings["substitution_bindings"]["port"] == port_var
    assert "cmdMetricsRollingStatsTime" in r.settings["raw"]


def test_http_connector_resource():
    r = resources.parse_resource(ORIG_RES / "ApplicationsConnector.httpConnResource")
    assert r.kind == "http"
    assert r.settings["role"] == "server"
    assert r.settings["port"] == 9081


def test_jms_resource_bw6_masks_password():
    r = resources.parse_resource(GMF / "DealerFunding/Resources/dealerfunding/GMFEMSConnection.jmsConnResource")
    assert r.kind == "jms"
    assert r.id == "DealerFunding.dealerfunding.GMFEMSConnection"
    assert r.settings["provider_url"] == "tcp://localhost:7222"
    assert r.settings["password"] == "***"
    assert "password" in r.masked_fields


def test_bw5_sharedhttp_keeps_global_var_refs():
    r = resources.parse_resource(GMF / "LegacyAddress" / "AddressService.sharedhttp")
    assert r.kind == "http"
    assert r.id == "LegacyAddress/AddressService"
    assert r.settings["host"] == "%%HttpHost%%"
    assert r.settings["port"] == "%%AddressServicePort%%"
    assert r.settings["timeout_ms"] == 20000


def test_bw5_sharedjdbc_public_sample():
    p = next(PUBLIC.glob("GMF_PoC_3_BW5_codereview_Apache2/**/*.sharedjdbc"))
    r = resources.parse_resource(p)
    assert r.kind == "jdbc"
    assert r.settings["jdbc_url"].startswith("jdbc:oracle:thin:")
    assert r.settings["driver"].startswith("oracle.jdbc")
    assert r.settings["max_connections"] == 10
    assert r.id.endswith("/Resources/JDBC Connection")


def test_bw5_sharedjms_public_sample_masks_credential():
    p = next(PUBLIC.glob("GMF_PoC_2_BW5_BW6_tibcobwblog/bw5.x/TIBCO_BW_AMQ/*.sharedjmscon"))
    r = resources.parse_resource(p)
    assert r.kind == "jms"
    assert r.settings["provider_url"] == "tcp://localhost:61616"
    assert r.settings["factory"].endswith("ActiveMQInitialContextFactory")
    assert r.settings["naming_credential"] == "***"
    assert "naming_credential" in r.masked_fields
    assert r.id == "TIBCO_BW_AMQ/AMQ Connection"


def test_all_public_and_synthetic_resources_parse():
    exts = {".jdbcResource", ".httpClientResource", ".httpConnResource", ".jmsConnResource",
            ".sharedjdbc", ".sharedhttp", ".sharedjmscon"}
    files = [p for root in (GMF, PUBLIC) for p in root.rglob("*") if p.suffix in exts]
    assert len(files) > 20
    for p in files:
        r = resources.parse_resource(p)
        assert r.id and r.name
        for k in r.masked_fields:
            assert r.settings[k] == "***"


def test_unsupported_resource_extension():
    with pytest.raises(ValueError):
        resources.parse_resource(GMF / "estate.json")


def test_xsd_root_elements():
    c = contracts.parse_contract(GMF / "CreditOrigination/Schemas/CreditApplication.xsd")
    assert c.kind == "xsd"
    assert c.id == "CreditOrigination/Schemas/CreditApplication.xsd"
    assert "CreditApplicationRequest" in c.root_elements
    assert "CreditApplicationResponse" in c.root_elements
    assert "AddressType" not in c.root_elements  # complexTypes are not roots
    assert "<xs:schema" in c.text


def test_wsdl_port_type_operations():
    p = PUBLIC / "GMF_PoC_2_BW5_BW6_tibcobwblog/bw6.x/tibco_bw_https/HttpsTest/Service Descriptors/Process.wsdl"
    c = contracts.parse_contract(p)
    assert c.kind == "wsdl"
    assert c.root_elements == ["Process.operation"]


def test_json_service_descriptor_definitions():
    c = contracts.parse_contract(
        GMF / "CreditOrigination/Service Descriptors/creditorigination.CreditApplicationIntake-applications.json")
    assert c.kind == "json"
    assert "CreditApplicationRequest" in c.root_elements
    assert "CreditApplicationResponse" in c.root_elements


def test_json_schema_title_and_defs(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('{"title": "Order", "$defs": {"Line": {}, "Address": {}}}')
    c = contracts.parse_contract(f)
    assert c.root_elements == ["Order", "Line", "Address"]


def test_unsupported_contract_extension(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    with pytest.raises(ValueError):
        contracts.parse_contract(f)


def test_crlf_file_reads(tmp_path):
    src = (GMF / "CreditOrigination/Schemas/CreditApplication.xsd").read_bytes()
    lf_root = xmlutil.read_xml(GMF / "CreditOrigination/Schemas/CreditApplication.xsd")
    crlf = tmp_path / "crlf.xsd"
    crlf.write_bytes(src.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert b"\r\n" in crlf.read_bytes()
    root = xmlutil.read_xml(crlf)
    assert xmlutil.local(root.tag) == "schema"
    # line numbers stay aligned with the LF version
    assert [e.sourceline for e in root] == [e.sourceline for e in lf_root]
    c = contracts.parse_contract(crlf)
    assert "\r" not in c.text
    assert "CreditApplicationRequest" in c.root_elements


def test_xmlutil_local_and_ns():
    assert xmlutil.local("{http://x}Foo") == "Foo"
    assert xmlutil.local("Bar") == "Bar"
    assert xmlutil.local(xmlutil.etree.Comment("c").tag) == ""
    for key in ("bpws", "tibex", "pd", "xsl", "xsd", "wsdl", "jdbc", "http", "jms", "tibprop"):
        assert xmlutil.NS[key].startswith("http")


def test_read_xml_keeps_sourceline():
    root = xmlutil.read_xml(GMF / "LegacyAddress/AddressService.sharedhttp")
    assert root.sourceline == 2
    assert root[0].sourceline == 3


def test_read_xml_does_not_resolve_external_entities(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    f = tmp_path / "evil.xml"
    f.write_text(f'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x SYSTEM "file://{secret}">]><a>&x;</a>')
    root = xmlutil.read_xml(f)
    assert "TOPSECRET" not in (root.text or "")
