FROM registry.access.redhat.com/ubi9/python-312:latest

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /opt/app-root/src

COPY --chown=1001:0 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=1001:0 app ./app

EXPOSE 8080

USER 1001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
