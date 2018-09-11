FROM "python:2.7"

ENV POSTGRES_HOST_PORT_ENV ""
ENV POSTGRES_USERNAME_ENV ""
ENV POSTGRES_PASSWORD_ENV ""

ENV RABBITMQ_HOST_PORT_ENV ""
ENV RABBITMQ_USERNAME_ENV ""
ENV RABBITMQ_PASSWORD_ENV ""

ENV MINION_WORKDIR_PATH_ENV "/tmp/jeeves-minion-work-dir"

RUN git clone https://github.com/jeeves-ci/jeeves-minion.git \
    && cd jeeves-minion \
    && git checkout master \
    && pip install -r requirements.txt -e .

# result files workdir
VOLUME /tmp/jeeves-minion-work-dir

# results socket port
EXPOSE 7777

WORKDIR jeeves-minion/
CMD ["python", "jeeves_minion/minion.py"]