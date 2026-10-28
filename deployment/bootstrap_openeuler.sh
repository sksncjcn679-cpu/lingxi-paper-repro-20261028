#!/bin/sh
set -eu
dnf install -y python3 python3-pip util-linux procps-ng iproute findutils which git tar gzip
mkdir -p /opt/lingxi/checkout /opt/lingxi/drivers
tar -xf /bootstrap/project.tar -C /opt/lingxi/checkout
if [ ! -e /opt/lingxi/tests ]; then ln -s /opt/lingxi/checkout/tests /opt/lingxi/tests; fi
python3 -m venv /opt/lingxi/venv
/opt/lingxi/venv/bin/python -m pip install --index-url https://pypi.org/simple -e '/opt/lingxi/checkout[dev,llm,api]'
/opt/lingxi/venv/bin/python -m pip check
