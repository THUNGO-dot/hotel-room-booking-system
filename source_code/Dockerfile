# Grand Horizon Hotel booking system (Group 07)
#   docker build -t hotel-booking .
#   docker run --rm -p 5000:5000 hotel-booking
# then open http://localhost:5000
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# the SQLite database and the e-mail outbox are written here
VOLUME ["/app/instance"]
EXPOSE 5000

# listen on all interfaces so the browser outside the container can reach it
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "5000"]
