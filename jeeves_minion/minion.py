import os
import json
import shlex
import socket
import logging
import datetime
from time import sleep

from jeeves_minion.stream.logs import LogStreamHttpServer
from jeeves_minion.docker_exec import DockerExecClient, DockerExecException

from jeeves_commons.storage.storage import (get_storage_client,
                                            create_storage_client)

from jeeves_commons.dsl import parser
from jeeves_commons.queue import publisher
from jeeves_commons.constants import (NUM_MINION_WORKERS_ENV,
                                      MINION_WORKDIR_PATH_ENV,
                                      RABBITMQ_HOST_IP_ENV,
                                      RABBITMQ_HOST_PORT_ENV,
                                      RABBITMQ_USERNAME_ENV,
                                      RABBITMQ_PASSWORD_ENV,
                                      POSTGRES_HOST_IP_ENV,
                                      POSTGRES_HOST_PORT_ENV,
                                      POSTGRES_USERNAME_ENV,
                                      POSTGRES_PASSWORD_ENV,
                                      DEFAULT_BROKER_PORT,
                                      DEFAULT_WORKDIR_PATH,
                                      DEFAULT_POSTGRES_PORT,
                                      DEFAULT_RESULT_SOCKET_PORT,
                                      POSTGRES_RESULTS_DB,
                                      MINION_TASKS_QUEUE)
from jeeves_commons.utils import open_channel, create_logger

from celery import Celery, bootsteps
from kombu import Consumer

logger = create_logger('minion_logger',
                       level=logging.DEBUG)

# Get the number of workers per Jeeves minion. Default is set to 4.
NUM_MINION_WORKERS = os.getenv(NUM_MINION_WORKERS_ENV, '4')
MINION_WORKDIR_PATH = os.getenv(MINION_WORKDIR_PATH_ENV, DEFAULT_WORKDIR_PATH)

CELERY_START_COMMAND = 'celery -A minion worker --concurrency {0} ' \
                       '--config jeeves_minion.celeryconfig ' \
                       '--logfile=/tmp/celery_logs.log ' \
                       '--prefetch-multiplier=1' \
                       .format(NUM_MINION_WORKERS)

# Get message broker details
MESSAGE_BROKER_HOST_IP = os.getenv(RABBITMQ_HOST_IP_ENV, '172.17.0.3')
MESSAGE_BROKER_HOST_PORT = int(os.getenv(RABBITMQ_HOST_PORT_ENV,
                                         DEFAULT_BROKER_PORT))
MESSAGE_BROKER_USERNAME = os.getenv(RABBITMQ_USERNAME_ENV, 'guest')
MESSAGE_BROKER_PASSWORD = os.getenv(RABBITMQ_PASSWORD_ENV, 'guest')

# Get the result handler details
RESULTS_BACKEND_HOST_IP = os.getenv(POSTGRES_HOST_IP_ENV, '172.17.0.2')
RESULTS_BACKEND_HOST_PORT = int(os.getenv(POSTGRES_HOST_PORT_ENV,
                                          DEFAULT_POSTGRES_PORT))
RESULTS_BACKEND_USERNAME = os.getenv(POSTGRES_USERNAME_ENV, 'postgres')
RESULTS_BACKEND_PASSWORD = os.getenv(POSTGRES_PASSWORD_ENV, 'postgres')

broker_url = 'amqp://{0}:{1}@{2}:{3}//'.format(MESSAGE_BROKER_USERNAME,
                                               MESSAGE_BROKER_PASSWORD,
                                               MESSAGE_BROKER_HOST_IP,
                                               MESSAGE_BROKER_HOST_PORT)

backend_url = 'db+postgresql://{0}:{1}@{2}:{3}/{4}'.format(
                                                RESULTS_BACKEND_USERNAME,
                                                RESULTS_BACKEND_PASSWORD,
                                                RESULTS_BACKEND_HOST_IP,
                                                RESULTS_BACKEND_HOST_PORT,
                                                POSTGRES_RESULTS_DB)

app = Celery(broker=broker_url,
             backend=backend_url)


# TODO: another task for stashing logs? in future..

@app.task(name="run_script_in_container", max_retries=0)
def execute_install_task(task_id):
    from jeeves_commons.storage.storage import create_storage_client
    storage_client = create_storage_client()

    task = storage_client.tasks.get(task_id=task_id)
    _handle_execution_start(task, storage_client)
    dependencies = json.loads(task.task_dependencies)
    task_obj = parser.get_task(json.loads(task.content))

    workdir = os.path.join(MINION_WORKDIR_PATH,
                           str(task.workflow_id),
                           task.task_id)
    log_file = os.path.join(workdir, '{0}.log'.format(task.task_id))
    exec_client = DockerExecClient(base_image=task_obj.env.image,
                                   workdir=workdir,
                                   log_file=log_file)

    try:
        env = json.loads(storage_client.workflows.
                         get(workflow_id=task.workflow_id).env)
        logger.debug('Executing pre-script for task \'{0}\' with ID {1}. '
                     'Env is: {2}'
                     .format(task.task_name, task.task_id, str(env)))
        pre_logs, pre_env = exec_client.run_script(
            task_obj.pre_script,
            env=env)

        # Wait for task dependencies
        logger.debug('Waiting for task {0} dependencies {1}...'
                     .format(task.task_name, task.task_dependencies))
        succeeded, failed = wait_for_tasks(dependencies, storage_client)
        logger.debug('Dependency results are (success: {0}, failure: {1})'
                     .format(succeeded, failed))
        if not failed:
            env = json.loads(storage_client.workflows.
                             get(workflow_id=task.workflow_id).env_result)
            env.update(pre_env)
            # Run script
            logger.debug('executing script for task \'{0}\' with ID {1}'
                         .format(task.task_name, task.task_id))
            script_logs, script_env = exec_client.run_script(
                task_obj.script,
                env=env,
                final=True)

            _handle_execution_success(task, script_env, storage_client)
            logger.debug('Task {} ended successfully'.format(task.task_name))
        else:
            msg = 'One or more task dependencies failed \'{0}\'.' \
                  ' Skipping execution of {1}.'.\
                  format(str(failed), task.task_name)
            raise DockerExecException(msg)
    except DockerExecException as e:
        logger.debug('Docker execution error: {}'.format(e.message))
        _handle_execution_error(task, e.message, storage_client)
        raise RuntimeError(e.message)
    except Exception as e:
        logger.debug('Task encountered and error: {}', e)
        _handle_execution_error(task, e.message, storage_client)
        raise RuntimeError(e.message)
    finally:
        storage_client.close()
        exec_client.close()

    output = '{pre_logs}\n{script_logs}'.format(pre_logs=pre_logs,
                                                script_logs=script_logs)
    return output


def _handle_execution_error(failed_task, error_msg, storage_client):
    storage_client.tasks.update(task_id=failed_task.task_id,
                                status='FAILURE',
                                result=error_msg)
    storage_client.workflows.update(failed_task.workflow_id,
                                    status='FAILURE',
                                    ended_at=datetime.datetime.now())
    storage_client.commit()
    logger.debug('Revoking task tree for task {0}'.format(failed_task.task_id))
    publisher.revoke_task_tree(failed_task)


def _handle_execution_success(task, env, storage_client):
    workflow = storage_client.workflows.get(workflow_id=task.workflow_id)
    workflow_env = json.loads(workflow.env_result)
    workflow_env.update(env)

    workflow_status = None
    workflow_end_time = None
    succeeded_tasks = storage_client.tasks.list(task.workflow_id,
                                                status='SUCCESS')
    if len(workflow.tasks) == len(succeeded_tasks) + 1:
        workflow_status = 'SUCCESS'
        workflow_end_time = datetime.datetime.now()

    storage_client.workflows.update(task.workflow_id,
                                    env_result=json.dumps(workflow_env),
                                    status=workflow_status,
                                    ended_at=workflow_end_time)
    storage_client.commit()


def _handle_execution_start(task, storage_client):
    # Update the task status
    storage_client.tasks.update(
        task_id=task.task_id,
        status='STARTED',
        started_at=str(datetime.datetime.now()),
        minion_ip=socket.gethostbyname(socket.gethostname()))

    workflow = storage_client.workflows.get(workflow_id=task.workflow_id)
    if workflow.status not in ('FAILURE', 'STARTED', 'REVOKED'):
        # Update the workflow status
        storage_client.workflows.update(
            task.workflow_id,
            status='STARTED',
            started_at=datetime.datetime.now())
    storage_client.commit()


def wait_for_tasks(task_ids, storage_client):
    if not task_ids:
        return [], []

    while True:
        succeeded, failed = _dependency_results(task_ids, storage_client)
        logger.debug('Failed: {0}. Success: {1}'.format(str(failed),
                                                        str(succeeded)))
        if len(succeeded) == len(task_ids) or len(failed) > 0:
            return succeeded, failed

        sleep(0.5)


def _dependency_results(dependencies, storage_client):
    succeeded = []
    failed = []
    for task_id in dependencies:
        task_item = storage_client.tasks.get(task_id)
        logger.debug('Task {0} status is: {1}'.format(task_item.task_name,
                                                      task_item.status))
        if task_item.status in ['FAILURE', 'REVOKED']:
            failed.append(task_item.task_name)
        elif task_item.status == 'SUCCESS':
            succeeded.append(task_item.task_name)

    return succeeded, failed


class MinionConsumerStep(bootsteps.ConsumerStep):

    def get_consumers(self, channel):
        return [Consumer(channel,
                         queues=[publisher.tasks_queue],
                         callbacks=[self.handle_message],
                         accept=['json'])]

    def handle_message(self, body, message):
        message.ack()
        task = get_storage_client().tasks.get(task_id=body)
        if task is None:
            logger.error('Task with ID {0} does not exist in DB'.format(body))
            return
        elif task.status == 'REVOKED_MANUALLY':
            logger.debug('Task with ID {0} was manually revoked'.format(body))
            return

        # Execute task async
        execute_install_task.apply_async(
                                   args=[task.task_id],
                                   task_id=task.task_id)


app.steps['consumer'].add(MinionConsumerStep)


class MinionBootstrapper(object):
    def __init__(self, app):
        self.app = app
        # init minion ip address
        self.minion_ip = socket.gethostbyname(socket.gethostname())
        # Create new minion in DB
        self._create_minion()
        # Create a minion queue
        self._create_queue()

    def _create_queue(self):
        with open_channel(MESSAGE_BROKER_HOST_IP,
                          MESSAGE_BROKER_HOST_PORT) as _channel:
            _channel.queue_declare(queue=MINION_TASKS_QUEUE,
                                   durable=True)

    def _create_minion(self):
        storage = create_storage_client()
        if not storage.minions.get(minion_ip=self.minion_ip):
            storage.minions.create(minion_ip=self.minion_ip,
                                   status='STARTED')
        storage.commit()
        storage.close()

    def start(self):
        # Start the tornado log streamer.
        # TODO: this solution is only valid when running locally in docker.
        #       Needs to be separated to a service.
        self._start_log_server()
        self.app.start(shlex.split(CELERY_START_COMMAND))

    @staticmethod
    def _start_log_server():
        streamer = LogStreamHttpServer(MINION_WORKDIR_PATH)
        streamer.start(DEFAULT_RESULT_SOCKET_PORT)


if __name__ == '__main__':
    # database.init_db()
    bs = MinionBootstrapper(app)
    # import yaml
    # from jeeves_commons.storage import utils as storage_utils
    # from jeeves_commons.random_constants import get_random_name
    # for i in range(10):
    #     with open(os.path.join(os.path.dirname(os.path.realpath(__file__)),
    #                            '../resources/examples',
    #                            'jeeves_workflow.yaml'),
    #               'r') as workflow_stream:
    #         workflow = yaml.load(workflow_stream)
    #     _, tasks = storage_utils.create_workflow(get_storage_client(),
    #                                              name=get_random_name(0),
    #                                              content=workflow,
    #                                              tenant_id=1)
    #     for task_item in tasks:
    #         publisher.send_task_message(task_item.task_id)
    bs.start()
