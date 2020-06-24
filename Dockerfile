FROM python:3.8-alpine3.12
COPY requirements.txt /srv/
WORKDIR /srv
RUN pip3 install -r requirements.txt
COPY prometheus-netbox-sd.py /srv/
CMD while true; do python3 /srv/prometheus-netbox-sd.py "$NETBOX_URL" "$NETBOX_TOKEN" "${SD_DIRECTORY}"; sleep ${INTERVAL:-15m}; done
