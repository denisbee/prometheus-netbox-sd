FROM python:3.8-alpine3.12
COPY requirements.txt /srv/
WORKDIR /srv
RUN apk add --no-cache gcc git libev-dev musl-dev libffi-dev libressl-dev make libressl libev && \
    pip install -r requirements.txt && \
    apk del --no-cache git gcc libev-dev musl-dev libffi-dev libressl-dev make
COPY prometheus-netbox-sd.py /srv/
ENTRYPOINT [ "/usr/local/bin/python3", "-u", "/srv/prometheus-netbox-sd.py" ]
