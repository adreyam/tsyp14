FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app/src:/app PYTHONUNBUFFERED=1 BRAIN_LOG_DIR=/logs
# default: the Brain. Override the command to run a mock or a scenario, e.g.
#   docker run ... python -m mocks.twin --host 0.0.0.0
#   docker run ... python -m scenarios.run all
CMD ["python", "-m", "energy_brain.run", "--config", "config/config.yaml"]
