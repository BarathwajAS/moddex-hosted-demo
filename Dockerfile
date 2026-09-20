FROM python:3.12-slim

WORKDIR /app
COPY . /app

ENV PYTHONUNBUFFERED=1
ENV MODDEX_REMOTE=1
ENV PORT=7860

EXPOSE 7860

CMD ["python", "-u", "ModStudio.py", "--noopen"]
