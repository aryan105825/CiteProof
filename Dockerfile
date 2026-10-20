# @exports: image serving uvicorn citeproof.api:app on port 8000
# @imports: requirements.txt, citeproof/, web/, results/ (copied if present)
# @env: CITEPROOF_MAILTO
# @env: CITEPROOF_RATE
# @env: CITEPROOF_TIMEOUT
# @env: CITEPROOF_CACHE
# @env: CITEPROOF_OPENALEX
# @schema: run with: docker build -t citeproof . && docker run -p 8000:8000 -e CITEPROOF_MAILTO=you@example.com citeproof
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "citeproof.api:app", "--host", "0.0.0.0", "--port", "8000"]
