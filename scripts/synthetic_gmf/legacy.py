"""BW5 LegacyAddress project."""
from .names import LA_ADDR, STATES

STATE_LIST = " " + STATES + " "

PROCESS = f'''<?xml version="1.0" encoding="UTF-8"?>
<pd:ProcessDefinition xmlns:pd="http://xmlns.tibco.com/bw/process/2003" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:tib="http://www.tibco.com/bw/xslt/custom-functions" xmlns:pfx="{LA_ADDR}">
    <xsd:import namespace="{LA_ADDR}" schemaLocation="/Schemas/Address.xsd"/>
    <pd:name>LegacyAddressStandardize.process</pd:name>
    <pd:description>Standardizes a US mailing address for the dealer portal (trim, upper case, state check).</pd:description>
    <pd:startName>HTTP Receiver</pd:startName>
    <pd:startX>0</pd:startX>
    <pd:startY>0</pd:startY>
    <pd:returnBindings/>
    <pd:starter name="HTTP Receiver">
        <pd:type>com.tibco.plugin.http.HTTPEventSource</pd:type>
        <pd:resourceType>httppalette.httpEventSource</pd:resourceType>
        <pd:x>72</pd:x>
        <pd:y>118</pd:y>
        <config>
            <outputMode>String</outputMode>
            <defaultEncoding>ISO8859_1</defaultEncoding>
            <inputOutputVersion>5.3.0</inputOutputVersion>
            <newMimeSupport>true</newMimeSupport>
            <sharedChannel>/AddressService.sharedhttp</sharedChannel>
            <parsePostData>true</parsePostData>
            <Headers>
                <xsd:element name="root">
                    <xsd:complexType>
                        <xsd:sequence>
                            <xsd:element name="Accept" type="xsd:string" minOccurs="0"/>
                            <xsd:element name="Accept-Charset" type="xsd:string" minOccurs="0"/>
                            <xsd:element name="Content-Type" type="xsd:string" minOccurs="0"/>
                            <xsd:element name="Content-Length" type="xsd:string" minOccurs="0"/>
                            <xsd:element name="Connection" type="xsd:string" minOccurs="0"/>
                        </xsd:sequence>
                    </xsd:complexType>
                </xsd:element>
            </Headers>
            <parameters>
                <parameter>
                    <name>line1</name>
                    <dataType>string</dataType>
                    <repeating>Required</repeating>
                </parameter>
                <parameter>
                    <name>line2</name>
                    <dataType>string</dataType>
                    <repeating>Optional</repeating>
                </parameter>
                <parameter>
                    <name>city</name>
                    <dataType>string</dataType>
                    <repeating>Required</repeating>
                </parameter>
                <parameter>
                    <name>state</name>
                    <dataType>string</dataType>
                    <repeating>Required</repeating>
                </parameter>
                <parameter>
                    <name>zip</name>
                    <dataType>string</dataType>
                    <repeating>Required</repeating>
                </parameter>
            </parameters>
        </config>
        <pd:inputBindings/>
    </pd:starter>
    <pd:endName>End</pd:endName>
    <pd:endX>612</pd:endX>
    <pd:endY>118</pd:endY>
    <pd:errorSchemas/>
    <pd:processVariables/>
    <pd:targetNamespace>http://xmlns.example.com/1238767341952</pd:targetNamespace>
    <pd:activity name="Standardize Address">
        <pd:type>com.tibco.plugin.mapper.MapperActivity</pd:type>
        <pd:resourceType>ae.activities.MapperActivity</pd:resourceType>
        <pd:x>238</pd:x>
        <pd:y>118</pd:y>
        <config>
            <element ref="pfx:StandardizedAddress"/>
        </config>
        <pd:inputBindings>
            <pfx:StandardizedAddress>
                <pfx:line1>
                    <xsl:value-of select="upper-case(tib:trim($HTTP-Receiver/ProcessStarterOutput/parameters/line1))"/>
                </pfx:line1>
                <xsl:if test="string-length(tib:trim($HTTP-Receiver/ProcessStarterOutput/parameters/line2)) &gt; 0">
                    <pfx:line2>
                        <xsl:value-of select="upper-case(tib:trim($HTTP-Receiver/ProcessStarterOutput/parameters/line2))"/>
                    </pfx:line2>
                </xsl:if>
                <pfx:city>
                    <xsl:value-of select="upper-case(tib:trim($HTTP-Receiver/ProcessStarterOutput/parameters/city))"/>
                </pfx:city>
                <pfx:state>
                    <xsl:value-of select="upper-case(tib:trim($HTTP-Receiver/ProcessStarterOutput/parameters/state))"/>
                </pfx:state>
                <pfx:postalCode>
                    <xsl:value-of select="substring(tib:trim($HTTP-Receiver/ProcessStarterOutput/parameters/zip), 1, 5)"/>
                </pfx:postalCode>
            </pfx:StandardizedAddress>
        </pd:inputBindings>
    </pd:activity>
    <pd:activity name="Send HTTP Response">
        <pd:type>com.tibco.plugin.http.HTTPResponseActivity</pd:type>
        <pd:resourceType>httppalette.httpWebResponse</pd:resourceType>
        <pd:x>452</pd:x>
        <pd:y>118</pd:y>
        <config>
            <closeConnection>true</closeConnection>
            <eventSource>HTTP Receiver</eventSource>
            <Headers>
                <xsd:element name="root">
                    <xsd:complexType>
                        <xsd:sequence>
                            <xsd:element name="StatusLine" type="xsd:string" minOccurs="0"/>
                            <xsd:element name="Content-Type" type="xsd:string" minOccurs="0"/>
                            <xsd:element name="Set-Cookie" type="xsd:string" minOccurs="0"/>
                            <xsd:element name="Pragma" type="xsd:string" minOccurs="0"/>
                            <xsd:element name="Location" type="xsd:string" minOccurs="0"/>
                        </xsd:sequence>
                    </xsd:complexType>
                </xsd:element>
            </Headers>
        </config>
        <pd:inputBindings>
            <ResponseActivityInput>
                <asciiContent>
                    <xsl:value-of select="tib:render-xml($Standardize-Address/pfx:StandardizedAddress)"/>
                </asciiContent>
                <Headers>
                    <StatusLine>
                        <xsl:value-of select="&quot;HTTP/1.1 200 OK&quot;"/>
                    </StatusLine>
                    <Content-Type>
                        <xsl:value-of select="&quot;text/xml; charset=ISO-8859-1&quot;"/>
                    </Content-Type>
                </Headers>
            </ResponseActivityInput>
        </pd:inputBindings>
    </pd:activity>
    <pd:transition>
        <pd:from>HTTP Receiver</pd:from>
        <pd:to>Standardize Address</pd:to>
        <pd:lineType>Default</pd:lineType>
        <pd:lineColor>-16777216</pd:lineColor>
        <pd:conditionType>always</pd:conditionType>
    </pd:transition>
    <pd:transition>
        <pd:from>Standardize Address</pd:from>
        <pd:to>Send HTTP Response</pd:to>
        <pd:xpathDescription>Valid state code</pd:xpathDescription>
        <pd:lineType>Default</pd:lineType>
        <pd:lineColor>-16777216</pd:lineColor>
        <pd:conditionType>xpath</pd:conditionType>
        <pd:xpath>string-length($Standardize-Address/pfx:StandardizedAddress/pfx:state) = 2 and contains('{STATE_LIST}', concat(' ', $Standardize-Address/pfx:StandardizedAddress/pfx:state, ' '))</pd:xpath>
    </pd:transition>
    <pd:transition>
        <pd:from>Send HTTP Response</pd:from>
        <pd:to>End</pd:to>
        <pd:lineType>Default</pd:lineType>
        <pd:lineColor>-16777216</pd:lineColor>
        <pd:conditionType>always</pd:conditionType>
    </pd:transition>
</pd:ProcessDefinition>
'''

SHAREDHTTP = '''<?xml version="1.0" encoding="UTF-8"?>
<ns0:httpSharedResource xmlns:ns0="www.tibco.com/shared/HTTPConnection">
    <config>
        <Host>%%HttpHost%%</Host>
        <serverType>Tomcat</serverType>
        <HttpConnectionProperties>
            <row>
                <PropertyName>maxPostSize (bytes)</PropertyName>
                <PropertyValue/>
                <PropertyDefaultValue>2097152</PropertyDefaultValue>
            </row>
            <row>
                <PropertyName>acceptCount</PropertyName>
                <PropertyValue>100</PropertyValue>
                <PropertyDefaultValue/>
            </row>
            <row>
                <PropertyName>connectionTimeOut (ms)</PropertyName>
                <PropertyValue>20000</PropertyValue>
                <PropertyDefaultValue>60000</PropertyDefaultValue>
            </row>
        </HttpConnectionProperties>
        <Port>%%AddressServicePort%%</Port>
    </config>
</ns0:httpSharedResource>
'''


def _gv(name, value, settable="true", typ="String", mod="1238767339120"):
    return ("\t\t<globalVariable>\n"
            f"\t\t\t<name>{name}</name>\n"
            f"\t\t\t<value>{value}</value>\n"
            f"\t\t\t<deploymentSettable>{settable}</deploymentSettable>\n"
            "\t\t\t<serviceSettable>false</serviceSettable>\n"
            f"\t\t\t<type>{typ}</type>\n"
            f"\t\t\t<modTime>{mod}</modTime>\n"
            "\t\t</globalVariable>\n")


DEFAULTVARS = ('<?xml version = "1.0" encoding = "UTF-8"?>\n'
               '<repository xmlns:xsi = "http://www.w3.org/2001/XMLSchema-instance" '
               'xmlns = "http://www.tibco.com/xmlns/repo/types/2002">\n\t<globalVariables>\n'
               + _gv("AddressServicePort", "8095", typ="Integer")
               + _gv("Deployment", "LegacyAddress")
               + _gv("DirLedger", ".")
               + _gv("DirTrace", ".")
               + _gv("Domain", "GMF_DEALER_DOMAIN")
               + _gv("HawkEnabled", "false")
               + _gv("HttpHost", "localhost")
               + _gv("JmsProviderUrl", "tcp://localhost:7222")
               + _gv("RvDaemon", "tcp:7500")
               + _gv("RvNetwork", "")
               + _gv("RvService", "7500")
               + _gv("TIBHawkDaemon", "tcp:7474")
               + _gv("TIBHawkNetwork", "")
               + _gv("TIBHawkService", "7474")
               + "\t</globalVariables>\n</repository>")

VCREPO = '''<?xml version="1.0" encoding="UTF-8"?>
<repository version="2" commitCount="7">
  <instanceInfo>
    <instanceInfoProperty name="displayName" value="LegacyAddress"/>
    <instanceInfoProperty name="version" value="5.13.0"/>
    <instanceInfoProperty name="rootDir" value="."/>
    <instanceInfoProperty name="instanceType" value="localFile"/>
    <instanceInfoProperty name="fileType" value="VC"/>
    <instanceInfoProperty name="creationTime" value="1238767339120"/>
    <instanceInfoProperty name="encoding" value="ISO8859-1"/>
  </instanceInfo>
</repository>
'''
