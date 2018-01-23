FROM mobify/python:3.6

RUN useradd --home-dir /osmaps osmaps

RUN mkdir /venv && \
    chown -R osmaps:osmaps /venv && \
    mkdir /osmaps && \
    chown -R osmaps:osmaps /osmaps

RUN pip install virtualenv

USER osmaps
WORKDIR /osmaps

COPY requirements.txt  liblonlat_bng.so.bz2 ./
RUN virtualenv /venv && \
    . /venv/bin/activate && \
    pip install --no-cache-dir -r /osmaps/requirements.txt

# Patch convertbng
# /venv/lib/python3.6/site-packages/convertbng/.libs/liblonlat_bng-783571af.so
RUN bunzip2 liblonlat_bng.so.bz2 && \
    find /venv -name 'liblonlat*.so' -exec cp -v liblonlat_bng.so {} \; && \
    rm -f liblonlat_bng.so

COPY *.png ./
COPY *.py ./

CMD /venv/bin/gunicorn --config gunicorn_config.py --access-logfile - --log-file - osmaps:APP
# CMD /venv/bin/python3.6 ./osmaps.py
