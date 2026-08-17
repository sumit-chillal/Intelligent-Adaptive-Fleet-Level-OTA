# ============================================================================
# CONVOY — simulated TCU image. ONE image, MANY containers.
# Identity and health come from environment variables at run time, never from
# a baked-in file, so a laptop runs five distinct devices from this one build.
# ============================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Every application module, not just the entrypoint. Listing files one by one
# means a new module silently fails to ship -- which is exactly what happened
# when ota.py was added and every container crashed with ModuleNotFoundError.
COPY tcu_agent.py ota.py ./

# Resume state lives in /data. A named volume is mounted here by compose, so a
# container killed mid-download resumes at its last verified chunk instead of
# restarting from zero.
#
# ORDER MATTERS: the user must be created and /data chowned BEFORE any VOLUME
# instruction. Docker discards filesystem changes made to a path after that
# path has been declared as a volume -- which silently leaves /data owned by
# root and makes the agent crash on its first state write.
# There is deliberately no VOLUME instruction here at all: the compose files
# declare the volumes, which keeps ownership under our control.
ENV STATE_DIR=/data
RUN useradd --create-home --uid 10001 tcu \
    && mkdir -p /data \
    && chown -R tcu:tcu /app /data

USER tcu

ENTRYPOINT ["python", "tcu_agent.py"]