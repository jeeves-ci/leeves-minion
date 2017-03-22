# CELERY_IMPORTS = ("jeeves_minion.tasks", )
CELERY_RESULT_BACKEND = "amqp"
CELERY_TASK_RESULT_EXPIRES = 300

BROKER_CONNECTION_MAX_RETRIES = 100
BROKER_CONNECTION_TIMEOUT = 1000

#
# CELERY_ROUTES = {
#     'jeeves_minion.tasks.add': {
#         'queue': 'jeeves_minions_queue',
#         'routing_key': ''
#     },
# }
