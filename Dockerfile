FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py queue_watch.py ./
# SCRIPT=queue_watch.py runs the immigration queue watcher instead of the main bot
CMD python ${SCRIPT:-bot.py}
