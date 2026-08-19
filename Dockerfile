FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 默认执行转发；可通过 docker-compose command 覆盖
CMD ["python", "main.py", "forward"]
