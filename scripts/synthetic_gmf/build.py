"""Write the whole synthetic GMF-style estate (idempotent: the target folder is rebuilt)."""
from __future__ import annotations

import shutil
from pathlib import Path

from . import co, data, df_ls, estate, legacy, project, schemas

ROOT = Path()


def w(rel: str, text: str):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    assert "\u2014" not in text, rel
    path.write_text(text, encoding="utf-8")


def module_common(module: str):
    w(f"{module}/META-INF/MANIFEST.MF", project.manifest(module))
    w(f"{module}/META-INF/module.bwm", project.module_bwm(module))
    w(f"{module}/META-INF/default.substvar", project.module_substvar(module))
    w(f"{module}/META-INF/module.jsv", project.JSV)
    w(f"{module}/META-INF/module.msv", project.MSV)
    w(f"{module}/.project", project.eclipse_project(module))
    w(f"{module}/.config", project.module_config(module))
    w(f"{module}/build.properties", project.BUILD_PROPERTIES)
    w(f"{module}/.settings/org.eclipse.pde.core.prefs", project.PDE_PREFS)
    w(f"{module}/Policies/.gitkeep", "")
    w(f"{module}/Schemas/RESTSchema.xsd", schemas.REST_SCHEMA)


def proc_file(p) -> str:
    pkg, name = p.name.split(".")
    return f"{p.module}/Processes/{pkg}/{name}.bwp"


def build(root: Path) -> int:
    """Regenerate every file under `root`; returns the number of files written."""
    global ROOT
    ROOT = Path(root)
    if ROOT.exists():
        shutil.rmtree(ROOT)
    w("SYNTHETIC.txt", estate.SYNTHETIC_TXT)

    app = "gmf.credit.application"
    w(f"{app}/META-INF/MANIFEST.MF", project.APP_MANIFEST)
    w(f"{app}/META-INF/TIBCO.xml", project.tibco_xml())
    w(f"{app}/META-INF/default.substvar", project.app_substvar())
    w(f"{app}/.project", project.eclipse_project(app, app=True))
    w(f"{app}/.config", project.app_config())

    for module in project.MODULES:
        module_common(module)

    m = "CreditOrigination"
    w(f"{m}/Schemas/CreditApplication.xsd", schemas.CREDIT_APPLICATION)
    w(f"{m}/Schemas/Bureau.xsd", schemas.BUREAU)
    w(f"{m}/Schemas/Decision.xsd", schemas.DECISION)
    w(f"{m}/Schemas/Vehicle.xsd", schemas.VEHICLE)
    w(f"{m}/Schemas/Audit.xsd", schemas.AUDIT)
    w(f"{m}/Schemas/Faults.xsd", schemas.CO_FAULTS)
    w(f"{m}/Service Descriptors/creditorigination.CreditApplicationIntake-applications.json",
      schemas.INTAKE_SWAGGER)
    res = f"{m}/Resources/creditorigination"
    w(f"{res}/GMFOriginationDB.jdbcResource",
      project.jdbc_resource("creditorigination.GMFOriginationDB", "svc_bw_orig"))
    w(f"{res}/EquifaxClient.httpClientResource",
      project.http_client_resource("creditorigination.EquifaxClient", "EQUIFAX.HOST",
                                   "EQUIFAX.PORT", "8091"))
    w(f"{res}/TransUnionClient.httpClientResource",
      project.http_client_resource("creditorigination.TransUnionClient", "TRANSUNION.HOST",
                                   "TRANSUNION.PORT", "8092"))
    w(f"{res}/ApplicationsConnector.httpConnResource",
      project.http_conn_resource("creditorigination.ApplicationsConnector",
                                 "ORIGINATION.HTTP.PORT", "9081"))

    m = "DealerFunding"
    w(f"{m}/Schemas/FundingRequest.xsd", schemas.FUNDING_REQUEST)
    w(f"{m}/Schemas/FundingEvent.xsd", schemas.FUNDING_EVENT)
    w(f"{m}/Schemas/FundingStatus.xsd", schemas.FUNDING_STATUS)
    w(f"{m}/Schemas/FundingParameterSchema.xsd", schemas.FUNDING_PARAMS)
    w(f"{m}/Schemas/Faults.xsd", schemas.DF_FAULTS)
    w(f"{m}/Service Descriptors/dealerfunding.FundingStatusLookup-funding.json",
      schemas.FUNDING_SWAGGER)
    res = f"{m}/Resources/dealerfunding"
    w(f"{res}/GMFFundingDB.jdbcResource",
      project.jdbc_resource("dealerfunding.GMFFundingDB", "svc_bw_funding"))
    w(f"{res}/GMFEMSConnection.jmsConnResource",
      project.jms_resource("dealerfunding.GMFEMSConnection", "svc_bw_funding"))
    w(f"{res}/FundingConnector.httpConnResource",
      project.http_conn_resource("dealerfunding.FundingConnector", "FUNDING.HTTP.PORT", "9082"))

    m = "LoanServicing"
    w(f"{m}/Schemas/Payment.xsd", schemas.PAYMENT)
    w(f"{m}/Schemas/Account.xsd", schemas.ACCOUNT)
    w(f"{m}/Schemas/AccountParameterSchema.xsd", schemas.ACCOUNT_PARAMS)
    w(f"{m}/Schemas/CollectionsReferral.xsd", schemas.COLLECTIONS)
    w(f"{m}/Service Descriptors/loanservicing.AccountLookup-accounts.json",
      schemas.ACCOUNT_SWAGGER)
    res = f"{m}/Resources/loanservicing"
    w(f"{res}/GMFServicingDB.jdbcResource",
      project.jdbc_resource("loanservicing.GMFServicingDB", "svc_bw_servicing"))
    w(f"{res}/GMFEMSConnection.jmsConnResource",
      project.jms_resource("loanservicing.GMFEMSConnection", "svc_bw_servicing"))
    w(f"{res}/AccountsConnector.httpConnResource",
      project.http_conn_resource("loanservicing.AccountsConnector", "SERVICING.HTTP.PORT", "9083"))

    for build in co.PROCESSES + df_ls.DF_PROCESSES + df_ls.LS_PROCESSES:
        p = build()
        w(proc_file(p), p.document())

    w("LegacyAddress/vcrepo.dat", legacy.VCREPO)
    w("LegacyAddress/LegacyAddressStandardize.process", legacy.PROCESS)
    w("LegacyAddress/AddressService.sharedhttp", legacy.SHAREDHTTP)
    w("LegacyAddress/defaultVars/defaultVars.substvar", legacy.DEFAULTVARS)
    w("LegacyAddress/Schemas/Address.xsd", schemas.ADDRESS)

    w("Database/schema.sql", data.SCHEMA_SQL)
    w("Database/seed.sql", data.seed_sql())
    w("Stubs/equifax.json", data.EQUIFAX)
    w("Stubs/transunion.json", data.TRANSUNION)
    w("estate.json", estate.estate_json(ROOT))
    return sum(1 for f in ROOT.rglob("*") if f.is_file())
