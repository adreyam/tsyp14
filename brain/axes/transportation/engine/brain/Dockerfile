# City Brain layer. SUMO comes from the eclipse-sumo wheel, so nothing else has to be installed.
FROM python:3.11-slim
WORKDIR /app
COPY requirements_city.txt ./
RUN pip install --no-cache-dir -r requirements_city.txt eclipse-sumo
ENV SUMO_HOME=/usr/local/lib/python3.11/site-packages/sumo
ENV PATH="${PATH}:/usr/local/lib/python3.11/site-packages/sumo/bin"
COPY . .
ENV PORT=8000 BRAIN_LOGS_DIR=/logs BRAIN_SCENARIOS_DIR=/scenarios PARKING_DB_URL=sqlite:////app/data/citybrain.db PYTHONUNBUFFERED=1
RUN mkdir -p /app/data /logs
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=12 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"
CMD ["python", "-m", "citybrain.server"]
