# skill-router — the server is three tools over a directory of Markdown, so the image
# is small on purpose: the MCP SDK, uvicorn, and the two provider SDKs for llm_router.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY skill_server.py skills.py llm_router.py ./
# The example catalog. Mount your own over /skills rather than rebuilding — editing a
# skill should not need a docker build.
COPY skills/ /skills/

# Non-root. Nothing here writes to the filesystem, so it does not need to be root and
# the catalog can go in read-only.
RUN useradd --system --uid 10001 app && chown -R app:app /app
USER app

# 0.0.0.0 because the container's own loopback is unreachable from outside it; the
# boundary is the published port and whatever proxy sits in front. The Host/Origin
# allow-lists still apply — set MCP_ALLOWED_HOSTS/ORIGINS to the hostname clients use.
ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8001 \
    MCP_PATH=/mcp \
    SKILLS_DIR=/skills

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status==200 else 1)"

ENTRYPOINT ["python", "skill_server.py"]
CMD ["--transport", "streamable-http"]
