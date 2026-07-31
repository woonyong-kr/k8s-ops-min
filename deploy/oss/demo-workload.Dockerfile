FROM python:3.13.13-alpine

USER 65532:65532
EXPOSE 8080

CMD ["python", "-m", "http.server", "8080", "--directory", "/tmp"]
