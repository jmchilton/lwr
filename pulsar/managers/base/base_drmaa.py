from .external import ExternalBaseManager
from ..util.drmaa import DrmaaSessionFactory
from pulsar.managers import status

try:
    from drmaa import JobState
except ImportError:
    JobState = None

import logging
log = logging.getLogger(__name__)


class BaseDrmaaManager(ExternalBaseManager):

    def __init__(self, name, app, **kwds):
        super(BaseDrmaaManager, self).__init__(name, app, **kwds)
        self.native_specification = kwds.get('native_specification', None)
        drmaa_session_factory_class = kwds.get('drmaa_session_factory_class', DrmaaSessionFactory)
        drmaa_session_factory = drmaa_session_factory_class()
        self.drmaa_session = drmaa_session_factory.get()

    def shutdown(self):
        try:
            super(BaseDrmaaManager, self).shutdown()
        except:
            pass
        self.drmaa_session.close()

    def _get_status_external(self, external_id):
        drmaa_state = self.drmaa_session.job_status(external_id)
        return {
            JobState.UNDETERMINED: status.COMPLETE,
            JobState.QUEUED_ACTIVE: status.QUEUED,
            JobState.SYSTEM_ON_HOLD: status.QUEUED,
            JobState.USER_ON_HOLD: status.QUEUED,
            JobState.USER_SYSTEM_ON_HOLD: status.QUEUED,
            JobState.RUNNING: status.RUNNING,
            JobState.SYSTEM_SUSPENDED: status.QUEUED,
            JobState.USER_SUSPENDED: status.QUEUED,
            JobState.DONE: status.COMPLETE,
            JobState.FAILED: status.COMPLETE,  # Should be a FAILED state here as well
        }[drmaa_state]

    def _build_template_attributes(self, job_id, command_line, dependencies_description=None, env=[], submit_params={}):
        stdout_path = self._stdout_path(job_id)
        stderr_path = self._stderr_path(job_id)

        attributes = {
            "remoteCommand": self._setup_job_file(job_id, command_line, dependencies_description=dependencies_description, env=env),
            "jobName": self._job_name(job_id),
            "outputPath": ":%s" % stdout_path,
            "errorPath": ":%s" % stderr_path,
        }
        if self.native_specification:
            attributes["nativeSpecification"] = self.native_specification
        elif submit_params.get("native_specification", None):
            attributes["nativeSpecification"] = submit_params["native_specification"]
        return attributes
