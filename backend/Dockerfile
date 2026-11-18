# Build context: repository root (docker build -f backend/Dockerfile .)
FROM node:22-alpine AS ui
WORKDIR /ui/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# SAP HANA client for the HANA SQL collector. hdbcli is distributed by SAP under the
# SAP Developer License; set HANA_CLIENT=false to build without it.
ARG HANA_CLIENT=true
# RFC collector. The SAP NW RFC SDK may not be redistributed: download it from SAP (customer/partner account),
# unpack it to backend/vendor/nwrfcsdk/ (lib/ and include/) and build with RFC_CLIENT=true. RFC_PACKAGE names the
# PyRFC-compatible binding to install (SAP's archived "pyrfc" builds against the SDK).
ARG RFC_CLIENT=false
ARG RFC_PACKAGE=pyrfc==3.3.1
ENV SAPNWRFC_HOME=/usr/local/sap/nwrfcsdk

WORKDIR /app
COPY backend/requirements.txt .
COPY backend/vendor/ /tmp/vendor/
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$HANA_CLIENT" = "true" ]; then pip install --no-cache-dir "hdbcli>=2.20"; fi \
    && if [ "$RFC_CLIENT" = "true" ]; then \
         test -d /tmp/vendor/nwrfcsdk/lib || { echo "RFC_CLIENT=true needs the SAP NW RFC SDK in backend/vendor/nwrfcsdk"; exit 1; }; \
         mkdir -p /usr/local/sap && cp -r /tmp/vendor/nwrfcsdk /usr/local/sap/nwrfcsdk \
         && echo /usr/local/sap/nwrfcsdk/lib > /etc/ld.so.conf.d/nwrfcsdk.conf && ldconfig \
         && apt-get update && apt-get install -y --no-install-recommends gcc g++ && pip install --no-cache-dir "$RFC_PACKAGE" \
         && apt-get purge -y gcc g++ && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*; \
       fi \
    && rm -rf /tmp/vendor
COPY backend/app ./app
COPY backend/alembic.ini .
COPY backend/migrations ./migrations
COPY abap ./abap
COPY --from=ui /ui/backend/app/static ./app/static

RUN useradd --create-home --uid 10001 sentinel \
    && mkdir -p /app/data \
    && chown -R sentinel:sentinel /app
USER sentinel

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
