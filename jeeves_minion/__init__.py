import os
from jeeves_commons.utils import wait_for_port

from jeeves_commons.constants import (RABBITMQ_HOST_IP_ENV,
                                      RABBITMQ_HOST_PORT_ENV,
                                      DEFAULT_BROKER_PORT)

print 'waiting for rabbitmq service...'
connected = wait_for_port(host=os.getenv(RABBITMQ_HOST_IP_ENV, '172.17.0.3'),
                          port=int(os.getenv(RABBITMQ_HOST_PORT_ENV,
                                             DEFAULT_BROKER_PORT)),
                          duration=30)
if not connected:
    raise RuntimeError('failed waiting for rabbitmq broker')
