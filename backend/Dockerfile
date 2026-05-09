FROM python:3.12-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV REGISTRATIONS_DB_PATH=/data/registrations.db

EXPOSE 8000

CMD ["python", "app.py"]
