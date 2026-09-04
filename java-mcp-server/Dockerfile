# Multi-stage: the JDK and the Maven cache stay in the builder, the runtime image
# carries a JRE and one jar.
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
# Dependencies first so a source-only change does not re-download Spring Boot.
COPY pom.xml .
RUN mvn -B -q dependency:go-offline
COPY src ./src
RUN mvn -B -q package -DskipTests

FROM eclipse-temurin:21-jre-jammy
WORKDIR /app
COPY --from=build /build/target/skill-router-mcp-server-*.jar app.jar

RUN useradd --system --uid 10001 mcp
USER mcp

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8002 \
    SKILLS_DIR=/skills

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD ["sh", "-c", "wget -qO- http://127.0.0.1:8002/actuator/health | grep -q UP"]

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
