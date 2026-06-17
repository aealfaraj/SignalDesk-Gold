FROM python:3.12-slim

WORKDIR /app

COPY . .

ENV HOST=0.0.0.0
ENV PORT=10000
ENV SIGNALDESK_SECURE_COOKIES=1
ENV SIGNALDESK_DATA_DIR=/var/data

RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /var/data

EXPOSE 10000

CMD ["python", "server.py"]
