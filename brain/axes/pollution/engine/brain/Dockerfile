# City Brain: scenarios, live demo and tests in one image (python:3.12-slim, no other system package).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
ENV CITY_LOG_DIR=/logs PYTHONUNBUFFERED=1
EXPOSE 8100 8200
# Default: every Test Book scenario (G1-G6, A1-A5); logs go to /logs.
CMD ["sh", "-c", "python -m demo.garbage all && python -m demo.air all"]
