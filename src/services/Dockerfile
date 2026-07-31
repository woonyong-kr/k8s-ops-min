FROM python:3.13.13-slim AS tools

ARG KUBECTL_VERSION=v1.36.2
ARG HELM_VERSION=v3.21.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && arch="$(dpkg --print-architecture)" \
    && case "${arch}" in \
      amd64) tool_arch="amd64" ;; \
      arm64) tool_arch="arm64" ;; \
      *) echo "unsupported image architecture for kubectl/helm: ${arch}" >&2; exit 1 ;; \
    esac \
    && curl -fsSLo /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${tool_arch}/kubectl" \
    && curl -fsSLo /tmp/kubectl.sha256 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${tool_arch}/kubectl.sha256" \
    && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/kubectl \
    && helm_archive="helm-${HELM_VERSION}-linux-${tool_arch}.tar.gz" \
    && curl -fsSLo "/tmp/${helm_archive}" \
      "https://get.helm.sh/${helm_archive}" \
    && curl -fsSLo "/tmp/${helm_archive}.sha256sum" \
      "https://get.helm.sh/${helm_archive}.sha256sum" \
    && cd /tmp \
    && sha256sum -c "${helm_archive}.sha256sum" \
    && tar -xzf "${helm_archive}" \
    && install -m 0755 "/tmp/linux-${tool_arch}/helm" /usr/local/bin/helm

FROM python:3.13.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin appuser

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=tools /usr/local/bin/kubectl /usr/local/bin/kubectl
COPY --from=tools /usr/local/bin/helm /usr/local/bin/helm
RUN kubectl version --client=true \
    && helm version --short

COPY src/services/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
COPY --chown=appuser:appuser deploy/target ./deploy/target
COPY --chown=appuser:appuser scripts/install-telemetry.sh ./scripts/install-telemetry.sh
COPY --chown=appuser:appuser scripts/install-telemetry.ps1 ./scripts/install-telemetry.ps1
COPY --chown=appuser:appuser src ./src
RUN find /app/src /app/deploy /app/scripts -type d -exec chmod 755 {} + \
    && find /app/src -type f -exec chmod 644 {} + \
    && find /app/deploy /app/scripts -type f -exec chmod 644 {} + \
    && test "$(alembic heads | wc -l | tr -d ' ')" = "1" \
    && alembic heads | grep -Eq '^[0-9_]+ \(head\)$' \
    && python -m packages.storage.baseline verify

# 비루트 실행 — 매니페스트 securityContext(runAsUser: 10001)와 UID 정렬.
# (root 로 의존성 설치/소스 복사 후 비특권 사용자로 전환; 8000/9100 은 비특권 포트)
USER 10001

EXPOSE 8000 9100

CMD ["python", "src/services/gateway/api-gateway/app.py"]
