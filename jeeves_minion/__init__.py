import os
from jeeves_commons.utils import wait_for_port

from jeeves_commons.constants import (RABBITMQ_HOST_IP_ENV,
                                      RABBITMQ_HOST_PORT_ENV,
                                      DEFAULT_BROKER_PORT)

try:
    rabbitmq_port = int(os.getenv(RABBITMQ_HOST_PORT_ENV,
                                  DEFAULT_BROKER_PORT))
except ValueError:
    rabbitmq_port = DEFAULT_BROKER_PORT


def wait():
    print 'waiting for rabbitmq service...'
    connected = wait_for_port(host=os.getenv(RABBITMQ_HOST_IP_ENV,
                                             '172.17.0.3'),
                              port=rabbitmq_port,
                              duration=60)
    if not connected:
        raise RuntimeError('failed waiting for rabbitmq broker')

wait()
