"""The Java dependency table (target-stacks spec section 4): every version the generated ``pom.xml``
names, pinned here once. No agent adds a dependency; Maven resolves exactly these from GMF's
mirror (``MAVEN_MIRROR_URL`` in ``.exiqo/maven-settings.xml``), and Maven has no lock format, so
nothing here is a range.

Spring Boot manages the Spring, Jackson, HikariCP, JUnit, AssertJ and Surefire versions through
``spring-boot-starter-parent``; the Azure SDK versions come from ``com.azure:azure-sdk-bom``.
"""
from __future__ import annotations

JAVA_RELEASE = 21
SPRING_BOOT = "3.5.7"  # the 3.5 line, at least 3.5.7 (global constraint)
AZURE_SDK_BOM = "1.2.36"  # azure-messaging-servicebus, azure-identity, azure-security-keyvault-secrets
POSTGRESQL = "42.7.7"  # org.postgresql:postgresql, the JDBC driver
JACOCO = "0.8.13"  # jacoco-maven-plugin: target/site/jacoco/jacoco.xml
SPOTLESS = "2.45.0"  # spotless-maven-plugin: the format gate (spotless:apply / spotless:check)
PALANTIR_JAVA_FORMAT = "2.67.0"  # the formatter Spotless runs

GROUP_ID = "com.gmf"

# Default base images of the generated Dockerfile; GMF passes its own registry's images as the
# BASE_IMAGE_BUILD / BASE_IMAGE_RUNTIME build arguments.
BASE_IMAGE_BUILD = f"maven:3.9-eclipse-temurin-{JAVA_RELEASE}"
BASE_IMAGE_RUNTIME = f"eclipse-temurin:{JAVA_RELEASE}-jre"

#: (groupId, artifactId, scope) of every dependency of the generated module, in pom order.
DEPENDENCIES: tuple[tuple[str, str, str], ...] = (
    ("org.springframework.boot", "spring-boot-starter-web", ""),
    ("org.springframework.boot", "spring-boot-starter-actuator", ""),
    ("org.springframework.boot", "spring-boot-starter-oauth2-resource-server", ""),
    ("org.springframework.boot", "spring-boot-starter-jdbc", ""),
    ("org.postgresql", "postgresql", "runtime"),
    ("com.azure", "azure-messaging-servicebus", ""),
    ("com.azure", "azure-identity", ""),
    ("com.azure", "azure-security-keyvault-secrets", ""),
    ("org.springframework.boot", "spring-boot-starter-test", "test"),
)
