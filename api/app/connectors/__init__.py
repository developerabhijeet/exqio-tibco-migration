"""Connectors to GMF-controlled GitHub, Atlassian (Confluence, Jira) and Figma services.

Two configuration layers (``config``): the install ceiling from the chart (``CONNECTORS``) and the
admin's Settings within it. Every request goes through the allowlisting client (``http``);
credentials are read from Key Vault mounted files on every call; every test and publish is
audited (``audit``) and every remote object it creates is recorded as a link (``links``).
"""
