FROM "python:2.7"

ENV POSTGRES_HOST_PORT_ENV ""
ENV POSTGRES_USERNAME_ENV ""
ENV POSTGRES_PASSWORD_ENV ""

ENV RABBITMQ_HOST_PORT_ENV ""
ENV RABBITMQ_USERNAME_ENV ""
ENV RABBITMQ_PASSWORD_ENV ""

RUN git clone https://github.com/jeeves-ci/jeeves-minion.git \
    && cd jeeves-minion \
    && git checkout 0.1 \
    && pip install -r requirements.txt .

# task result files
# VOLUME /tmp

# task results socket port
EXPOSE 7777

CMD ["python", "jeeves_minion/minion.py"]