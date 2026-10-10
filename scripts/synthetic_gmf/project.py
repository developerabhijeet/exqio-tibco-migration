"""Module, application and shared-resource descriptors."""
from __future__ import annotations

from .bwlib import obfuscated, xmi
from .bwproc import PRODUCT

BW_PROPS = [
    ("BW.DEPLOYMENTUNIT.TYPE", "string", ""), ("BW.APPNODE.NAME", "string", ""),
    ("BW.DEPLOYMENTUNIT.NAME", "string", ""), ("BW.HOST.NAME", "string", "localhost"),
    ("BW.DOMAIN.NAME", "string", ""), ("BW.DEPLOYMENTUNIT.VERSION", "string", ""),
    ("BW.APPSPACE.NAME", "string", ""), ("BW.MODULE.VERSION", "string", ""),
    ("BW.CLOUD.PORT", "int", "8080"), ("BW.MODULE.NAME", "string", ""),
]

MODULES = {
    "CreditOrigination": {
        "pkg": "creditorigination",
        "props": [("GMF.DB.URL", "string", "jdbc:postgresql://localhost:5432/gmf"),
                  ("GMF.DB.USER", "string", "svc_bw_orig"),
                  ("EQUIFAX.HOST", "string", "localhost"), ("EQUIFAX.PORT", "int", "8091"),
                  ("TRANSUNION.HOST", "string", "localhost"), ("TRANSUNION.PORT", "int", "8092"),
                  ("ORIGINATION.HTTP.PORT", "int", "9081")],
        "palettes": ["bw.generalactivities", "bw.jdbc", "bw.http", "bw.restjson"],
        "shared": ["bw.jdbc", "bw.httpclient", "bw.httpconnector"],
        "bindings": ["bw.rest"],
        "rest": [("CreditApplicationIntake", "applications", "/applications", "post", "POST",
                  "Applications", "20190314093512", [])],
        "components": ["CreditApplicationIntake"],
    },
    "DealerFunding": {
        "pkg": "dealerfunding",
        "props": [("GMF.DB.URL", "string", "jdbc:postgresql://localhost:5432/gmf"),
                  ("GMF.DB.USER", "string", "svc_bw_funding"),
                  ("EMS.PROVIDER.URL", "string", "tcp://localhost:7222"),
                  ("EMS.USER", "string", "svc_bw_funding"),
                  ("FUNDING.HTTP.PORT", "int", "9082")],
        "palettes": ["bw.generalactivities", "bw.jdbc", "bw.jms"],
        "shared": ["bw.jdbc", "bw.jms", "bw.httpconnector"],
        "bindings": ["bw.rest"],
        "rest": [("FundingStatusLookup", "funding", "/funding/{dealId}", "get", "GET", "Funding",
                  "20200224151530", ["dealId"])],
        "components": ["DealerFundingRequest", "FundingStatusLookup"],
    },
    "LoanServicing": {
        "pkg": "loanservicing",
        "props": [("GMF.DB.URL", "string", "jdbc:postgresql://localhost:5432/gmf"),
                  ("GMF.DB.USER", "string", "svc_bw_servicing"),
                  ("EMS.PROVIDER.URL", "string", "tcp://localhost:7222"),
                  ("EMS.USER", "string", "svc_bw_servicing"),
                  ("SERVICING.HTTP.PORT", "int", "9083")],
        "palettes": ["bw.generalactivities", "bw.jdbc", "bw.jms"],
        "shared": ["bw.jdbc", "bw.jms", "bw.httpconnector"],
        "bindings": ["bw.rest"],
        "rest": [("AccountLookup", "accounts", "/accounts/{accountNumber}", "get", "GET",
                  "Accounts", "20200615113902", ["accountNumber"])],
        "components": ["PaymentPosting", "AccountLookup", "NightlyDelinquencyScan"],
    },
}


def manifest(module: str) -> str:
    m = MODULES[module]
    caps = ['com.tibco.bw.model; filter:="(name=bwext)"']
    caps += [f'com.tibco.bw.palette; filter:="(name={x})"' for x in m["palettes"]]
    caps += [f'com.tibco.bw.sharedresource.model; filter:="(name={x})"' for x in m["shared"]]
    caps += [f'com.tibco.bw.binding.model; filter:="(name={x})"' for x in m["bindings"]]
    req = ",\n ".join(caps)
    return (f"Manifest-Version: 1.0\nBundle-ManifestVersion: 2\nBundle-Name: {module}\n"
            f"Bundle-SymbolicName: {module}\nBundle-Version: 1.0.0.qualifier\n"
            "Bundle-Vendor: TIBCO Software Inc.\n"
            "TIBCO-BW-ApplicationModule: META-INF/module.bwm\n"
            "TIBCO-BW-ConfigProfile: META-INF/default.substvar\n"
            "TIBCO-BW-Edition: bwe\n"
            "TIBCO-BW-JobSharedVariables: META-INF/module.jsv\n"
            f"Require-Capability: {req}\n"
            "TIBCO-BW-ModuleSharedVariables: META-INF/module.msv\n"
            f"TIBCO-BW-Version: {PRODUCT}\n")


def module_bwm(module: str) -> str:
    m = MODULES[module]
    pkg = m["pkg"]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           ('<sca:composite xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
           'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           'xmlns:BW="http://xsd.tns.tibco.com/amf/models/sca/implementationtype/BW" '
           'xmlns:XMLSchema="http://www.w3.org/2001/XMLSchema" '
           'xmlns:compositeext="http://schemas.tibco.com/amx/3.0/compositeext" '
           'xmlns:rest="http://xsd.tns.tibco.com/bw/models/binding/rest" '
           'xmlns:sca="http://www.osoa.org/xmlns/sca/1.0" '
           'xmlns:scact="http://xsd.tns.tibco.com/amf/models/sca/componentType" '
           'xmlns:scaext="http://xsd.tns.tibco.com/amf/models/sca/extensions" '
           f'xmi:id="{xmi(module + "/composite")}" '
           f'targetNamespace="http://tns.tibco.com/bw/composite/{module}" name="{module}" '
           'compositeext:version="1.0.0" compositeext:formatVersion="2">')]
    for proc, svc, path, op, verb, doc, ts, params in m["rest"]:
        wsdl = f"http://xmlns.example.com/{ts}PLT#wsdl.interface({svc})"
        loc = f"../Processes/{pkg}/{proc}.bwp"
        k = f"{module}/{svc}"
        out.append(f'  <sca:service xmi:id="{xmi(k + "/svc")}" name="{svc}" '
                   f'promote="Component{proc}/{svc}">')
        out.append(f'    <sca:interface.wsdl xmi:id="{xmi(k + "/iface")}" interface="{wsdl}" '
                   f'scaext:wsdlLocation="{loc}"/>')
        port = {"CreditOrigination": 9081, "DealerFunding": 9082, "LoanServicing": 9083}[module]
        out.append(f'    <scaext:binding xsi:type="rest:RestServiceBinding" xmi:id="{xmi(k + "/binding")}" '
                   f'name="RestService" path="{path}" docBasePath="http://localhost:{port}/{module}" '
                   f'docResourcePath="{doc}" basePath="/" connector="{pkg}.{doc}Connector" '
                   'structuredData="true" technologyVersion="2.0" '
                   f'implementation="Service Descriptors/{pkg}.{proc}-{svc}.json">')
        out.append(f'      <operation xmi:id="{xmi(k + "/op")}" operationName="{op}" '
                   f'nickname="{op}-{svc}" httpMethod="{verb}" responseStyle="element" '
                   'requestStyle="element">')
        out.append("        <clientFormat>json</clientFormat>")
        out.append("        <clientRequestFormat>json</clientRequestFormat>")
        out.append("      </operation>")
        if params:
            out.append(f'      <parameters xmi:id="{xmi(k + "/params")}">')
            for prm in params:
                out.append(f'        <parameterDefinitions xmi:id="{xmi(k + "/p/" + prm)}" '
                           f'name="{prm}" type="string" parameterType="Path" required="true"/>')
            out.append("      </parameters>")
        else:
            out.append(f'      <parameters xmi:id="{xmi(k + "/params")}"/>')
        out.append(f'      <advancedConfig xmi:id="{xmi(k + "/adv")}" blockingQueueSize="2147483647"/>')
        out.append("    </scaext:binding>")
        out.append(f'    <scact:bindingAdjunct xmi:id="{xmi(k + "/adj")}" bindingName="RestService"/>')
        out.append("  </sca:service>")
    for name, typ, _v in BW_PROPS:
        out.append(f'  <sca:property xmi:id="{xmi(module + "/prop/" + name)}" name="{name}" '
                   f'type="XMLSchema:{typ}" scalable="true"/>')
    for name, typ, _v in m["props"]:
        out.append(f'  <sca:property xmi:id="{xmi(module + "/prop/" + name)}" name="{name}" '
                   f'type="XMLSchema:{typ}" publicAccess="true" scalable="true"/>')
    rest_by_proc = {r[0]: r for r in m["rest"]}
    for proc in m["components"]:
        k = f"{module}/component/{proc}"
        out.append(f'  <sca:component xmi:id="{xmi(k)}" name="Component{proc}" '
                   'compositeext:version="1.0.0.qualifier">')
        out.append(f'    <scaext:implementation xsi:type="BW:BWComponentImplementation" '
                   f'xmi:id="{xmi(k + "/impl")}" processName="{pkg}.{proc}"/>')
        if proc in rest_by_proc:
            _p, svc, _path, _op, _verb, _doc, ts, _params = rest_by_proc[proc]
            wsdl = f"http://xmlns.example.com/{ts}PLT#wsdl.interface({svc})"
            out.append(f'    <sca:service xmi:id="{xmi(k + "/svc")}" name="{svc}">')
            out.append(f'      <sca:interface.wsdl xmi:id="{xmi(k + "/iface")}" interface="{wsdl}" '
                       f'scaext:wsdlLocation="../Processes/{pkg}/{proc}.bwp"/>')
            out.append("    </sca:service>")
        out.append("  </sca:component>")
    out.append("</sca:composite>")
    return "\n".join(out)


def _gv(name, value, typ, settable="false"):
    t = {"string": "String", "int": "Integer", "password": "Password"}[typ]
    return ("\t\t<globalVariable>\n"
            f"\t\t\t<name>{name}</name>\n"
            f"\t\t\t<value>{value}</value>\n"
            f"\t\t\t<deploymentSettable>{settable}</deploymentSettable>\n"
            "\t\t\t<serviceSettable>false</serviceSettable>\n"
            f"\t\t\t<type>{t}</type>\n"
            "\t\t\t<isOverride>false</isOverride>\n"
            "\t\t</globalVariable>\n")


SUBSTVAR_HEAD = ('<?xml version = "1.0" encoding = "UTF-8"?>\n'
                 '<repository xmlns:xsi = "http://www.w3.org/2001/XMLSchema-instance" '
                 'xmlns = "http://www.tibco.com/xmlns/repo/types/2002">\n\t<globalVariables>\n')
SUBSTVAR_TAIL = "\t</globalVariables>\n</repository>"


def module_substvar(module: str) -> str:
    rows = [_gv(n, v, t) for n, t, v in BW_PROPS]
    rows += [_gv(n, v, t) for n, t, v in MODULES[module]["props"]]
    return SUBSTVAR_HEAD + "".join(rows) + SUBSTVAR_TAIL


def app_substvar() -> str:
    rows = []
    for module, m in MODULES.items():
        rows += [_gv(f"//{module}//{n}", v, t) for n, t, v in BW_PROPS]
        rows += [_gv(f"//{module}//{n}", v, t) for n, t, v in m["props"]]
    return SUBSTVAR_HEAD + "".join(rows) + SUBSTVAR_TAIL


def tibco_xml() -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           ('<packaging:packageUnit xmlns:packaging="http://schemas.tibco.com/tra/model/core/PackagingModel" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema">'),
           ("  <packaging:description>gmf.credit: dealer channel origination, dealer funding and "
            "loan servicing</packaging:description>"),
           "  <packaging:properties>"]
    for module, m in MODULES.items():
        for name, typ, _v in BW_PROPS:
            out += _pkg_prop(module, name, typ, "private")
        for name, typ, _v in m["props"]:
            out += _pkg_prop(module, name, typ, "public")
    out.append("  </packaging:properties>")
    out.append("  <packaging:modules>")
    for module in MODULES:
        out += ["    <packaging:module>",
                f"      <packaging:symbolicName>{module}</packaging:symbolicName>",
                "      <packaging:technologyType>osgi-bundle,bw-appmodule</packaging:technologyType>",
                "      <packaging:technologyVersion>1.0.0.qualifier</packaging:technologyVersion>",
                "    </packaging:module>"]
    out.append("  </packaging:modules>")
    out.append("</packaging:packageUnit>")
    return "\n".join(out)


def _pkg_prop(module, name, typ, visibility):
    return ["    <packaging:property>",
            f"      <packaging:name>//{module}//{name}</packaging:name>",
            f"      <packaging:type>xsd:{typ}</packaging:type>",
            f"      <packaging:visibility>{visibility}</packaging:visibility>",
            "      <packaging:scalable>true</packaging:scalable>",
            "      <packaging:overrideValue>false</packaging:overrideValue>",
            "    </packaging:property>"]


APP_MANIFEST = ("Manifest-Version: 1.0\nBundle-ManifestVersion: 2\nBundle-Name: gmf.credit\n"
                "Bundle-SymbolicName: gmf.credit.application\nBundle-Version: 1.0.0.qualifier\n"
                "Bundle-Vendor: TIBCO Software Inc.\n"
                f"TIBCO-BW-Version: {PRODUCT}\nTIBCO-BW-Edition: bwe\n"
                "TIBCO-BW-Application: META-INF/TIBCO.xml\n"
                "TIBCO-BW-ConfigProfile: META-INF/default.substvar\n")


def eclipse_project(name, app=False) -> str:
    if app:
        builders = ["com.tibco.xpd.validation.validationBuilder", "com.tibco.zion.dc.ValidationBuilder"]
        natures = ["com.tibco.zion.dc.ApplicationNature", "com.tibco.xpd.resources.xpdNature"]
    else:
        builders = ["org.eclipse.pde.ManifestBuilder", "org.eclipse.pde.SchemaBuilder",
                    "com.tibco.bw.ManifestBuilder", "com.tibco.bw.ProcessBuilder",
                    "com.tibco.xpd.validation.validationBuilder", "com.tibco.bw.ValidationBuilder",
                    "com.tibco.zion.dc.builder.ModulePropertyBuilder",
                    "com.tibco.bw.ActivityReportBuilder",
                    "com.tibco.bw.rest.binding.builder.RestBindingBuilder"]
        natures = ["org.eclipse.pde.PluginNature", "com.tibco.bw.design.bwNature",
                   "com.tibco.xpd.resources.xpdNature"]
    b = "".join(f"\t\t<buildCommand>\n\t\t\t<name>{x}</name>\n\t\t\t<arguments>\n"
                "\t\t\t</arguments>\n\t\t</buildCommand>\n" for x in builders)
    n = "".join(f"\t\t<nature>{x}</nature>\n" for x in natures)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<projectDescription>\n'
            f"\t<name>{name}</name>\n\t<comment></comment>\n\t<projects>\n\t</projects>\n"
            f"\t<buildSpec>\n{b}\t</buildSpec>\n\t<natures>\n{n}\t</natures>\n"
            "</projectDescription>\n")


def module_config(module) -> str:
    k = module
    folders = [("com.tibco.amx.zion.schema", "Schemas"), ("com.tibco.bw.process.folder", "Processes"),
               ("wsdl", "Service Descriptors"), ("com.tibco.amx.zion.resource", "Resources"),
               ("com.tibco.amx.zion.policy", "Policies"), ("com.tibco.bw.module.folder", "META-INF")]
    rows = "\n".join(f'    <config:folder id="{xmi(k + "/folder/" + loc)}" kind="{kind}" location="{loc}"/>'
                     for kind, loc in folders)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<config:ProjectConfig xmlns:config="http://www.tibco.com/XPD/projectConfig">\n'
            f'  <projectDetails id="com.gmf.credit.{module.lower()}">\n    <globalDestinations/>\n'
            "    <edition>bwe</edition>\n  </projectDetails>\n"
            '  <config:assetTypes id="com.tibco.amx.zion.schema.asset"/>\n'
            '  <config:assetTypes id="com.tibco.bw.process.asset"/>\n'
            '  <config:assetTypes id="com.tibco.xpd.asset.wsdl"/>\n'
            '  <config:assetTypes id="com.tibco.amx.zion.resource.asset"/>\n'
            '  <config:assetTypes id="com.tibco.bw.core.design.policy.asset"/>\n'
            f"  <config:specialFolders>\n{rows}\n  </config:specialFolders>\n"
            "</config:ProjectConfig>\n")


def app_config() -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<config:ProjectConfig xmlns:config="http://www.tibco.com/XPD/projectConfig">\n'
            '  <projectDetails id="gmf.credit.application">\n    <globalDestinations/>\n'
            "  </projectDetails>\n  <config:specialFolders>\n"
            f'    <config:folder id="{xmi("app/folder")}" kind="com.tibco.bw.packageUnit.folder" '
            'location="META-INF"/>\n  </config:specialFolders>\n</config:ProjectConfig>\n')


BUILD_PROPERTIES = ("bin.includes = .,\\\n               .settings/,\\\n               META-INF/,\\\n"
                    "               Policies/,\\\n               Processes/,\\\n"
                    "               Resources/,\\\n               Schemas/,\\\n"
                    "               Service Descriptors/\n")
PDE_PREFS = ("eclipse.preferences.version=1\n"
             "manifest.exportWizard=com.tibco.bw.core.design.process.editor.module.export.wizard\n")
JSV = ('<?xml version="1.0" encoding="UTF-8"?>\n'
       '<jsv:DocumentRoot xmlns:jsv="http://tns.tibco.com/bw/model/core/jobSV">\n'
       "  <jobSharedVariables/>\n</jsv:DocumentRoot>")
MSV = ('<?xml version="1.0" encoding="UTF-8"?>\n'
       '<msv:DocumentRoot xmlns:msv="http://tns.tibco.com/bw/model/core/moduleSV">\n'
       "  <moduleSharedVariables/>\n</msv:DocumentRoot>")


# --- shared resources --------------------------------------------------------------

def jdbc_resource(name, user) -> str:
    k = name
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<jndi:namedResource xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:jdbc="http://xsd.tns.tibco.com/amf/models/sharedresource/jdbc" '
            'xmlns:jndi="http://xsd.tns.tibco.com/amf/models/sharedresource/jndi" '
            'xmlns:subject="http://xsd.tns.tibco.com/trinity/models/identity/subject" '
            f'xmi:id="{xmi(k)}" name="{name}" type="jdbc:JdbcDataSource">\n'
            f'  <jndi:reference xmi:id="{xmi(k + "/ref")}" key="subjectJndiName" '
            'type="subject:SubjectConfiguration" value=""/>\n'
            f'  <jndi:configuration xsi:type="jdbc:JdbcDataSource" xmi:id="{xmi(k + "/cfg")}" '
            f'username="{user}" password="{obfuscated(k)}" maxConnections="10" loginTimeout="30">\n'
            f'    <connectionConfig xsi:type="jdbc:NonXaConnection" xmi:id="{xmi(k + "/conn")}" '
            'jdbcDriver="org.postgresql.Driver" dbURL="jdbc:postgresql://localhost:5432/gmf">\n'
            f'      <substitutionBindings xmi:id="{xmi(k + "/sb1")}" template="dbURL" propName="GMF.DB.URL"/>\n'
            "    </connectionConfig>\n"
            f'    <substitutionBindings xmi:id="{xmi(k + "/sb2")}" template="username" propName="GMF.DB.USER"/>\n'
            "  </jndi:configuration>\n</jndi:namedResource>")


def http_client_resource(name, host_prop, port_prop, port) -> str:
    k = name
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<jndi:namedResource xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:http="http://xsd.tns.tibco.com/bw/models/sharedresource/httpclient" '
            'xmlns:jndi="http://xsd.tns.tibco.com/amf/models/sharedresource/jndi" '
            f'xmi:id="{xmi(k)}" name="{name}" type="http:HttpClientConfiguration">\n'
            f'  <jndi:configuration xsi:type="http:HttpClientConfiguration" xmi:id="{xmi(k + "/cfg")}" '
            'httpClientVersion="httpcomponents" cmdExecutionIsolationStrategy="THREAD" '
            'cmdExecutionIsolationTimeout="1000" cmdExecutionTimeoutEnabled="true" '
            'cmdExecutionIsolationInterruptOnTimeout="true" '
            'cmdExecutionIsolationSemaphoreMaxConcRequests="10" '
            'cmdCircuitBreakerRequestVolumeThreshold="20" cmdCircuitBreakerSleepWindow="5000" '
            'cmdCircuitBreakerErrorThresholdPercentage="50" cmdMetricsRollingStatsTime="10000" '
            'cmdMetricsRollingStatsNumBuckets="10" cmdMetricsHealthSnapshotInterval="500" '
            'cmdRequestLogEnabled="true" tpCoreSize="10" tpMaxQueueSize="-1" '
            'tpQueueSizeRejectionThreshold="5" tpKeepAliveTime="1">\n'
            f'    <tcpDetails xmi:id="{xmi(k + "/tcp")}" port="{port}">\n'
            f'      <substitutionBindings xmi:id="{xmi(k + "/sb1")}" template="host" propName="{host_prop}"/>\n'
            f'      <substitutionBindings xmi:id="{xmi(k + "/sb2")}" template="port" propName="{port_prop}"/>\n'
            "    </tcpDetails>\n  </jndi:configuration>\n</jndi:namedResource>")


def http_conn_resource(name, port_prop, port) -> str:
    k = name
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<jndi:namedResource xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:httpconnector="http://xsd.tns.tibco.com/bw/models/sharedresource/httpconnector" '
            'xmlns:jndi="http://xsd.tns.tibco.com/amf/models/sharedresource/jndi" '
            f'xmi:id="{xmi(k)}" name="{name}" type="httpconnector:HttpConnectorConfiguration">\n'
            f'  <jndi:configuration xsi:type="httpconnector:HttpConnectorConfiguration" '
            f'xmi:id="{xmi(k + "/cfg")}" port="{port}">\n'
            f'    <substitutionBindings xmi:id="{xmi(k + "/sb1")}" template="host" propName="BW.HOST.NAME"/>\n'
            f'    <substitutionBindings xmi:id="{xmi(k + "/sb2")}" template="port" propName="{port_prop}"/>\n'
            "  </jndi:configuration>\n</jndi:namedResource>")


def jms_resource(name, user_seed) -> str:
    k = name
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<jndi:namedResource xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:jms="http://xsd.tns.tibco.com/bw/models/sharedresource/jms" '
            'xmlns:jndi="http://xsd.tns.tibco.com/amf/models/sharedresource/jndi" '
            f'xmi:id="{xmi(k)}" name="{name}" type="jms:JMSConnectionFactory">\n'
            f'  <jndi:configuration xsi:type="jms:JMSConnectionFactory" xmi:id="{xmi(k + "/cfg")}" '
            'connectionFactoryType="Direct" messagingStyle="Generic" '
            'providerURL="tcp://localhost:7222" '
            f'username="{user_seed}" password="{obfuscated(k)}" clientID="">\n'
            f'    <substitutionBindings xmi:id="{xmi(k + "/sb1")}" template="providerURL" propName="EMS.PROVIDER.URL"/>\n'
            f'    <substitutionBindings xmi:id="{xmi(k + "/sb2")}" template="username" propName="EMS.USER"/>\n'
            "  </jndi:configuration>\n</jndi:namedResource>")
