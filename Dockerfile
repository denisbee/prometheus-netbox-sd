FROM python:3.8-alpine3.12
COPY requirements.txt /srv/
WORKDIR /srv
RUN pip3 install -r requirements.txt
COPY prometheus-netbox-sd.py /srv/
ENTRYPOINT ["/usr/local/bin/python3", "/srv/prometheus-netbox-sd.py" ]