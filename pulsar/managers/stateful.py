import datetime
import os
import time
import threading

from pulsar.managers import ManagerProxy
from pulsar.managers import status
from .staging import preprocess
from .staging import postprocess

import logging
log = logging.getLogger(__name__)

DEFAULT_DO_MONITOR = False

DECACTIVATE_FAILED_MESSAGE = "Failed to deactivate job with job id %s. May be problems when starting Pulsar next."
ACTIVATE_FAILED_MESSAGE = "Failed to activate job wiht job id %s. This job may not recover properly upon Pulsar restart."

JOB_FILE_FINAL_STATUS = "final_status"
JOB_FILE_POSTPROCESSED = "postprocessed"
JOB_FILE_PREPROCESSED = "preprocessed"
JOB_METADATA_RUNNING = "running"

DEFAULT_MIN_POLLING_INTERVAL = 0.5


class StatefulManagerProxy(ManagerProxy):
    """
    """

    def __init__(self, manager, **manager_options):
        super(StatefulManagerProxy, self).__init__(manager)
        min_polling_interval = manager_options.get("min_polling_interval", DEFAULT_MIN_POLLING_INTERVAL)
        self.min_polling_interval = datetime.timedelta(0, min_polling_interval)
        self.active_jobs = ActiveJobs(manager)
        self.__state_change_callback = lambda status, job_id: None
        self.__recover_active_jobs()
        self.__monitor = None

    def set_state_change_callback(self, state_change_callback):
        self.__state_change_callback = state_change_callback
        self.__monitor = ManagerMonitor(self)

    @property
    def name(self):
        return self._proxied_manager.name

    def setup_job(self, *args, **kwargs):
        job_id = self._proxied_manager.setup_job(*args, **kwargs)
        return job_id

    def handle_remote_staging(self, job_id, staging_config):
        job_directory = self._proxied_manager.job_directory(job_id)
        job_directory.store_metadata("staging_config", staging_config)

    def launch(self, job_id, *args, **kwargs):
        job_directory = self._proxied_manager.job_directory(job_id)

        def do_preprocess():
            try:
                staging_config = job_directory.load_metadata("staging_config", {})
                preprocess(job_directory, staging_config.get("setup", []))
                self._proxied_manager.launch(job_id, *args, **kwargs)
                with job_directory.lock("status"):
                    job_directory.store_metadata(JOB_FILE_PREPROCESSED, True)
                self.active_jobs.activate_job(job_id)
            except Exception:
                log.exception("Failed job preprocess for %s:", job_id)
                self.__state_change_callback(status.FAILED, job_id)

        new_thread_for_manager(self, "preprocess", do_preprocess, daemon=False)

    def get_status(self, job_id):
        """ Compute status used proxied manager and handle state transitions
        and track additional state information needed.
        """
        job_directory = self._proxied_manager.job_directory(job_id)
        with job_directory.lock("status"):
            proxy_status, state_change = self.__proxy_status(job_directory, job_id)

        if state_change == "to_complete":
            self.__deactivate(job_id, proxy_status)
        elif state_change == "to_running":
            self.__state_change_callback(status.RUNNING, job_id)

        return self.__status(job_directory, proxy_status)

    def __proxy_status(self, job_directory, job_id):
        """ Determine state with proxied job manager and if this job needs
        to be marked as deactivated (this occurs when job first returns a
        complete status from proxy.
        """
        state_change = None
        if not job_directory.has_metadata(JOB_FILE_PREPROCESSED):
            proxy_status = status.PREPROCESSING
        elif job_directory.has_metadata(JOB_FILE_FINAL_STATUS):
            proxy_status = job_directory.load_metadata(JOB_FILE_FINAL_STATUS)
        else:
            proxy_status = self._proxied_manager.get_status(job_id)
            if proxy_status == status.RUNNING:
                if not job_directory.has_metadata(JOB_METADATA_RUNNING):
                    job_directory.store_metadata(JOB_METADATA_RUNNING, True)
                    state_change = "to_running"
            elif proxy_status in [status.COMPLETE, status.CANCELLED]:
                job_directory.store_metadata(JOB_FILE_FINAL_STATUS, proxy_status)
                state_change = "to_complete"
        return proxy_status, state_change

    def __status(self, job_directory, proxy_status):
        """ Use proxied manager's status to compute the real
        (stateful) status of job.
        """
        if proxy_status == status.COMPLETE:
            if not job_directory.has_metadata(JOB_FILE_POSTPROCESSED):
                job_status = status.POSTPROCESSING
            else:
                job_status = status.COMPLETE
        else:
            job_status = proxy_status
        return job_status

    def __deactivate(self, job_id, proxy_status):
        self.active_jobs.deactivate_job(job_id)
        deactivate_method = getattr(self._proxied_manager, "_deactivate_job", None)
        if deactivate_method:
            try:
                deactivate_method(job_id)
            except Exception:
                log.exception("Failed to deactivate via proxied manager job %s" % job_id)
        if proxy_status == status.COMPLETE:
            self.__handle_postprocessing(job_id)

    def __handle_postprocessing(self, job_id):
        def do_postprocess():
            postprocess_success = False
            try:
                postprocess_success = postprocess(self._proxied_manager.job_directory(job_id))
            except Exception:
                log.exception("Failed to postprocess results for job id %s" % job_id)
            final_status = status.COMPLETE if postprocess_success else status.FAILED
            self.__state_change_callback(final_status, job_id)
        new_thread_for_manager(self, "postprocess", do_postprocess, daemon=False)

    def shutdown(self):
        if self.__monitor:
            try:
                self.__monitor.shutdown()
            except Exception:
                log.exception("Failed to shutdown job monitor for manager %s" % self.name)
        super(StatefulManagerProxy, self).shutdown()

    def __recover_active_jobs(self):
        recover_method = getattr(self._proxied_manager, "_recover_active_job", None)
        if recover_method is None:
            return

        for job_id in self.active_jobs.active_job_ids():
            try:
                recover_method(job_id)
            except Exception:
                log.exception("Failed to recover active job %s" % job_id)


class ActiveJobs(object):
    """ Keeps track of active jobs (those that are not yet "complete").
    Current implementation is file based, but could easily be made
    database-based instead.

    TODO: Keep active jobs in memory after initial load so don't need to repeatedly
    hit disk to recover this information.
    """

    def __init__(self, manager):
        persistence_directory = manager.persistence_directory
        if persistence_directory:
            active_job_directory = os.path.join(persistence_directory, "%s-active-jobs" % manager.name)
            if not os.path.exists(active_job_directory):
                os.makedirs(active_job_directory)
        else:
            active_job_directory = None
        self.active_job_directory = active_job_directory

    def active_job_ids(self):
        job_ids = []
        if self.active_job_directory:
            job_ids = os.listdir(self.active_job_directory)
        return job_ids

    def activate_job(self, job_id):
        if self.active_job_directory:
            path = self._active_job_file(job_id)
            try:
                open(path, "w").close()
            except Exception:
                log.warn(ACTIVATE_FAILED_MESSAGE % job_id)

    def deactivate_job(self, job_id):
        if self.active_job_directory:
            path = self._active_job_file(job_id)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    log.warn(DECACTIVATE_FAILED_MESSAGE % job_id)

    def _active_job_file(self, job_id):
        return os.path.join(self.active_job_directory, job_id)


class ManagerMonitor(object):
    """ Monitors active jobs of a StatefulManagerProxy.
    """

    def __init__(self, stateful_manager):
        self.stateful_manager = stateful_manager
        self.active = True
        thread = new_thread_for_manager(self, "monitor", self._run, True)
        self.thread = thread

    def shutdown(self):
        self.active = False
        self.thread.join()

    def _run(self):
        """ Main loop, repeatedly checking active jobs of stateful manager.
        """
        while self.active:
            try:
                self._monitor_active_jobs()
            except Exception:
                log.exception("Failure in stateful manager monitor step.")

    def _monitor_active_jobs(self):
        active_job_ids = self.stateful_manager.active_jobs.active_job_ids()
        iteration_start = datetime.datetime.now()
        for active_job_id in active_job_ids:
            try:
                self._check_active_job_status(active_job_id)
            except Exception:
                log.exception("Failed checking active job status for job_id %s" % active_job_id)
        iteration_end = datetime.datetime.now()
        iteration_length = iteration_end - iteration_start
        if iteration_length < self.stateful_manager.min_polling_interval:
            to_sleep = (self.stateful_manager.min_polling_interval - iteration_length)
            time.sleep(to_sleep.total_seconds())

    def _check_active_job_status(self, active_job_id):
        # Manager itself will handle state transitions when status changes,
        # just need to poll get_statu
        self.stateful_manager.get_status(active_job_id)


def new_thread_for_manager(manager, name, target, daemon):
    thread_name = "%s-%s" % (manager, name)
    thread = threading.Thread(name=thread_name, target=target)
    thread.daemon = daemon
    thread.start()
    return thread

__all__ = [StatefulManagerProxy]
