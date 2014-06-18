import os
import stat

JOB_FILE_RETURN_CODE = "return_code"
JOB_FILE_STANDARD_OUTPUT = "stdout"
JOB_FILE_STANDARD_ERROR = "stderr"
JOB_FILE_TOOL_ID = "tool_id"
JOB_FILE_TOOL_VERSION = "tool_version"

from pulsar.managers.base import BaseManager
from pulsar.managers import LWR_UNKNOWN_RETURN_CODE
from ..util.job_script import job_script
from ..util.env import env_to_statement


class DirectoryBaseManager(BaseManager):

    def _job_file(self, job_id, name):
        return self._job_directory(job_id)._job_file(name)

    def return_code(self, job_id):
        return_code_str = self._read_job_file(job_id, JOB_FILE_RETURN_CODE, default=LWR_UNKNOWN_RETURN_CODE)
        return int(return_code_str) if return_code_str and return_code_str != LWR_UNKNOWN_RETURN_CODE else return_code_str

    def stdout_contents(self, job_id):
        return self._read_job_file(job_id, JOB_FILE_STANDARD_OUTPUT, default="")

    def stderr_contents(self, job_id):
        return self._read_job_file(job_id, JOB_FILE_STANDARD_ERROR, default="")

    def _stdout_path(self, job_id):
        return self._job_file(job_id, JOB_FILE_STANDARD_OUTPUT)

    def _stderr_path(self, job_id):
        return self._job_file(job_id, JOB_FILE_STANDARD_ERROR)

    def _return_code_path(self, job_id):
        return self._job_file(job_id, JOB_FILE_RETURN_CODE)

    def _setup_job_for_job_id(self, job_id, tool_id, tool_version):
        self._setup_job_directory(job_id)

        tool_id = str(tool_id) if tool_id else ""
        tool_version = str(tool_version) if tool_version else ""

        authorization = self._get_authorization(job_id, tool_id)
        authorization.authorize_setup()

        self._write_tool_info(job_id, tool_id, tool_version)
        return job_id

    def _read_job_file(self, job_id, name, **kwds):
        return self._job_directory(job_id).read_file(name, **kwds)

    def _write_job_file(self, job_id, name, contents):
        return self._job_directory(job_id).write_file(name, contents)

    def _write_return_code(self, job_id, return_code):
        self._write_job_file(job_id, JOB_FILE_RETURN_CODE, str(return_code))

    def _write_tool_info(self, job_id, tool_id, tool_version):
        job_directory = self._job_directory(job_id)
        job_directory.store_metadata(JOB_FILE_TOOL_ID, tool_id)
        job_directory.store_metadata(JOB_FILE_TOOL_VERSION, tool_version)

    def _open_standard_output(self, job_id):
        return self._job_directory(job_id).open_file(JOB_FILE_STANDARD_OUTPUT, 'w')

    def _open_standard_error(self, job_id):
        return self._job_directory(job_id).open_file(JOB_FILE_STANDARD_ERROR, 'w')

    def _check_execution_with_tool_file(self, job_id, command_line):
        tool_id = self._tool_id(job_id)
        self._check_execution(job_id, tool_id, command_line)

    def _tool_id(self, job_id):
        tool_id = None
        job_directory = self._job_directory(job_id)
        if job_directory.has_metadata(JOB_FILE_TOOL_ID):
            tool_id = job_directory.load_metadata(JOB_FILE_TOOL_ID)
        return tool_id

    # Helpers methods related to setting up job script files.
    def _setup_job_file(self, job_id, command_line, dependencies_description=None, env=[]):
        command_line = self._expand_command_line(command_line, dependencies_description)
        script_env = self._job_template_env(job_id, command_line=command_line, env=env)
        script = job_script(**script_env)
        return self._write_job_script(job_id, script)

    def _job_template_env(self, job_id, command_line=None, env=[]):
        return_code_path = self._return_code_path(job_id)
        # TODO: Add option to ignore remote env.
        env = env + self.env_vars
        env_setup_commands = map(env_to_statement, env)
        job_template_env = {
            'job_instrumenter': self.job_metrics.default_job_instrumenter,
            'galaxy_lib': self._galaxy_lib(),
            'env_setup_commands': env_setup_commands,
            'exit_code_path': return_code_path,
            'working_directory': self.job_directory(job_id).working_directory(),
            'job_id': job_id,
        }
        if command_line:
            job_template_env['command'] = command_line

        return job_template_env

    def _write_job_script(self, job_id, contents):
        self._write_job_file(job_id, "command.sh", contents)
        script_path = self._job_file(job_id, "command.sh")
        os.chmod(script_path, stat.S_IEXEC | stat.S_IWRITE | stat.S_IREAD)
        return script_path
