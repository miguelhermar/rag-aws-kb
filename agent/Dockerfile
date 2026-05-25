FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY *.py /app/

EXPOSE 8080

CMD ["python", "app.py"]
