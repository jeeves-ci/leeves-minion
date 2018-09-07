import os
import json
import time

import docker


EXEC_COMMAND_TEMPLATE = ('echo "set -e" >> {file} && '
                         'echo \'{script}\' >> {file} && '
                         'chmod +x {file} && '
                         'source {file}')
EXEC_OUTPUT_FILE_NAME = 'output.log'
PRE_ENV_FILE_NAME = 'pre_env.sh'
POST_ENV_FILE_NAME = 'post_env.sh'


class DockerExecClient(object):
    def __init__(self, base_image, workdir, log_file):
        self.base_image = base_image
        self.api = docker.from_env().api

        # create workdir
        if not os.path.isdir(workdir):
            try:
                os.makedirs(workdir)
            except OSError as err:
                if err.errno != 17:
                    raise
        self.workdir = workdir

        # create logfile
        if not os.path.isfile(log_file):
            with open(log_file, 'a'):
                os.utime(log_file, None)
        self.exec_out_log = log_file
        self.container_id = None

    def get_workdir(self):
        return self.workdir

    def create_container(self, image):
        container = self.api.create_container(
            image=image,
            command='cat',
            detach=True,
            stdin_open=True,
            volumes={self.workdir: {}},
            host_config=self.api.create_host_config(
                binds={
                    self.workdir: {
                        'bind': self.workdir,
                        'ro': False,
                    },
                },
                network_mode='bridge'),
        )

        self.container_id = container['Id']
        return self.container_id

    # whats up with timeout?
    def run_script(self, script, env={}, final=False):
        if not self._image_exists():
            self._pull_image()

        if not self.container_id:
            self.container_id = self.create_container(image=self.base_image)
            self.api.start(self.container_id)

        task_exec = self._create_exec(script, env)

        # with streamer.start(self.exec_out_log, 7777):
        self.api.exec_start(task_exec)
        exec_res = self.api.exec_inspect(task_exec)

        output = self._read_output()
        if exec_res['ExitCode'] != 0:
            self._write('exist code: {}'.format(exec_res['ExitCode']), 'red')
            raise DockerExecException(message='Script execution failed.',
                                      result=output)
        if final:
            self._write('all is well that ends well..')
        result_env = self._create_env_result()
        return output, result_env

    def _read_output(self):
        with open(self.exec_out_log, 'r') as f:
            output = f.read()
        return output

    def _write(self, message, color='green'):
        with open(self.exec_out_log, 'a') as f:
            f.write('<font color="{0}">{1}</font>'.
                    format(color, message + os.linesep))

    def _create_exec(self, script, env):
        pre_dump_env_cmd = self._create_dump_env_command(PRE_ENV_FILE_NAME)
        inject_env_cmd = self._create_env_vars_command(env, stdout=False)
        run_task_cmd = self._create_run_task_command(script=script)
        post_dump_env_cmd = self._create_dump_env_command(POST_ENV_FILE_NAME)
        command = self._merge_bash_commands(pre_dump_env_cmd,
                                            inject_env_cmd,
                                            run_task_cmd,
                                            post_dump_env_cmd)

        # Return execution instance
        return self.api.exec_create(self.container_id,
                                    cmd=['/bin/bash', '-c', '-e', command])

    def _create_env_vars_command(self, env_vars, stdout):
        env_script = '#!/bin/bash{0}'.format('\n')
        for key, val in env_vars.iteritems():
            env_script += 'export {0}={1}{2}'.format(key, val, '\n')

        env_file = os.path.join(self.workdir, 'env.sh')
        command = EXEC_COMMAND_TEMPLATE.format(script=env_script,
                                               file=env_file)
        if stdout:
            command += ' >>{0} 2>&1'.format(self.exec_out_log)
        print 'Create env command created: {0}'.format(command)
        return command

    def _create_run_task_command(self, script):
        create_time = (str(time.time())).replace('.', '')
        task_file = os.path.join(self.workdir,
                                 'task_{time_stamp}.sh'
                                 .format(time_stamp=create_time))
        command = EXEC_COMMAND_TEMPLATE.format(script=script,
                                               file=task_file)
        command += ' >>{0} 2>&1'.format(self.exec_out_log)
        return command

    def _convert_env_file_to_dict(self, env_file):
        env = {}
        with open(os.path.join(self.workdir, env_file)) as f:
            for line in f.readlines():
                key, val = line.split('=')
                env[key] = val.strip('\n')
        return env

    def _create_env_result(self):
        env = {}
        pre_env = self._convert_env_file_to_dict(PRE_ENV_FILE_NAME)
        post_env = self._convert_env_file_to_dict(POST_ENV_FILE_NAME)

        for key, val in post_env.iteritems():
            if not pre_env.get(key):
                env[key] = val
        return env

    def _create_dump_env_command(self, dump_file):
        return 'echo "$(env)" >> {0}/{1}'.format(self.workdir,
                                                 dump_file)

    @staticmethod
    def _merge_bash_commands(*args):
        result = ''
        for command in args:
            result += '{0} && '.format(command)
        return result[:-4]

    def _image_exists(self):
        images = [item for item in self.api.images() if item['RepoTags']]
        image = [item for item in images if self.base_image in
                 item.get('RepoTags', [])]
        return image != []

    def _pull_image(self, stream=None):
        image_name, tag = self.base_image.split(':')
        if stream:
            for line in self.api.pull(image_name, tag=tag, stream=True):
                progress = json.loads(line).get('progress', None)
                if progress:
                    # Consider aggregating to task logs.
                    print (json.dumps(json.loads(line).get('progress'),
                                      indent=4))
        else:
            self.api.pull(image_name, tag=tag, stream=False)

    def safe_close(self):
        if self.container_id:
            running_containers = [container['Id'] for container in
                                  self.api.containers(all=True)]
            if self.container_id in running_containers:
                self.api.remove_container(self.container_id, force=True)

    def close(self):
        if self.container_id:
            try:
                self.api.remove_container(self.container_id, force=True)
            except Exception as e:
                print 'Failed removing container with ID: {0}. Reason: {1}'\
                    .format(self.container_id, e.message)


class DockerExecException(Exception):
    def __init__(self, message, result=None):
        Exception.__init__(self)
        self.message = message
        self.result = result
