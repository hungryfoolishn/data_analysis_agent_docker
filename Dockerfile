FROM telebi-telebi-base:latest

LABEL maintainer="DeepAnalyze"
LABEL description="DeepAnalyze data analysis agent (LangGraph + LangChain)"

USER root

# Apt: Chinese fonts (matplotlib CJK rendering) + tini for signal handling
# gosu: drop root -> telebi in entrypoint AFTER fixing bind-mount ownership
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        fonts-wqy-microhei \
        fonts-wqy-zenhei \
        tini \
        gosu \
        bash \
    && rm -rf /var/lib/apt/lists/*

# Create conda env matching local smolagents env (python 3.10)
RUN /opt/conda/bin/conda create -y -n smolagents python=3.10 pip \
    && /opt/conda/bin/conda clean -afy

# Put smolagents env first on PATH
ENV PATH=/opt/conda/envs/smolagents/bin:$PATH
ENV CONDA_DEFAULT_ENV=smolagents
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Matplotlib config dir (default /home/telebi/.config may not exist/writable)
ENV MPLCONFIGDIR=/tmp/matplotlib-cache

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt /app/requirements.txt
COPY langgraph_langchain/requirements-langgraph.txt /app/langgraph_langchain/requirements-langgraph.txt
RUN pip install --no-cache-dir -r requirements.txt -r langgraph_langchain/requirements-langgraph.txt

# Copy project code
COPY . /app/

# Ensure runtime dirs exist and are owned by telebi (will be volume-mounted at runtime)
RUN mkdir -p /app/workspace /app/temp_uploads \
    && chown -R telebi:telebi /app

# NOTE: do NOT set USER telebi here. Bind-mounted workspace/ and temp_uploads/
# arrive at runtime with the host's ownership (often root), which the non-root
# telebi cannot write to. The entrypoint starts as root, chowns those mounts,
# then drops to telebi via gosu before launching the app.

# Default env values (overridable at runtime)
ENV MAX_CONCURRENT_AGENTS=3 \
    SESSION_TTL_HOURS=24 \
    FILE_SERVER_BASE=http://localhost:8888 \
    API_BASE_URL=http://localhost:8888

# Override the base image's inherited HEALTHCHECK, which probes
# http://0.0.0.0:8001/health (a telebi service this image does not run) and so
# always fails, marking the container unhealthy even though our backend is up.
# Probe the actual FastAPI backend (port 8888) instead.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8888/health || exit 1

EXPOSE 8888 8501

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker-entrypoint.sh"]
